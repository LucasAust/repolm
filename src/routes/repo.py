"""
RepoLM — Repo CRUD, ingest, upload, and file endpoints.
"""

import json
import os
import uuid
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import TOKEN_COSTS
from auth import get_current_user
import db_async
import state
from concurrency import ingest_queue, acquire_ingest
from routes._helpers import check_rate_limit
from services.ingestion import (
    ingest_repo, repo_to_text, should_skip_file, should_skip_dir,
    detect_language, MAX_FILE_SIZE, MAX_TOTAL_CHARS, PRIORITY_FILES,
)

router = APIRouter()
logger = logging.getLogger("repolm")


def _reconstruct_files_from_text(repo_text, file_index):
    """Reconstruct file list from repo_text (## path\\n```ext\\ncontent\\n```)."""
    import re
    files = []
    # Parse sections like: ## path/to/file.py\n```py\ncontent\n```
    sections = re.split(r'\n## ', repo_text)
    index_map = {f["path"]: f for f in (file_index or [])}
    for section in sections:
        section = section.strip()
        if not section:
            continue
        lines = section.split('\n', 1)
        path = lines[0].strip()
        if not path or path.startswith('#'):
            continue
        content = ""
        if len(lines) > 1:
            body = lines[1]
            # Extract content from code fence
            match = re.search(r'```\w*\n(.*?)```', body, re.DOTALL)
            if match:
                content = match.group(1)
            else:
                content = body.strip()
        idx = index_map.get(path, {})
        files.append({
            "path": path,
            "content": content,
            "size": idx.get("size", len(content)),
            "is_priority": idx.get("is_priority", path in ("README.md", "main.py", "index.js")),
        })
    return files


async def find_cached_repo_any(url):
    """Find a cached repo by URL via Postgres."""
    return await db_async.find_cached_repo_by_url(url)


def run_ingest(repo_id, url):
    """Background worker: clone and process a repo. Runs in thread pool (sync is fine)."""
    try:
        def _progress(status, message):
            try:
                db_async.sync_update_job(repo_id, status=status, message=message)
            except Exception:
                pass

        data = ingest_repo(url, progress_callback=_progress)
        db_async.sync_update_job(repo_id, status="processing", message="Building summary ({} files)...".format(len(data.files)))
        text = repo_to_text(data)
        file_list = [{"path": f.path, "content": f.content, "size": f.size, "is_priority": f.is_priority} for f in data.files]
        repo_data = {
            "name": data.name, "url": url, "tree": data.tree,
            "total_chars": data.total_chars, "file_count": len(data.files),
            "skipped": data.skipped_count,
            "languages": dict(sorted(data.language_stats.items(), key=lambda x: -x[1])[:10]),
        }
        repo_entry = {
            "status": "ready", "message": "Ready",
            "data": repo_data, "files": file_list, "text": text,
        }
        state.repos.set(repo_id, repo_entry)
        db_async.sync_cache_repo_to_db(repo_id, repo_entry)
        db_async.sync_update_job(repo_id, status="ready", message="Ready",
                           result=json.dumps(repo_data))
    except Exception as e:
        logger.exception("Ingest failed for %s", url)
        db_async.sync_update_job(repo_id, status="error", message=str(e))


def run_upload_ingest(repo_id, files_data):
    """Process uploaded folder files. Runs in thread pool (sync is fine)."""
    from ingest import RepoFile, RepoData
    try:
        db_async.sync_update_job(repo_id, status="processing", message="Processing uploaded files...")

        paths = [f["path"] for f in files_data]
        folder_name = paths[0].split("/")[0] if paths and "/" in paths[0] else "uploaded-project"

        processed = []
        language_stats = {}
        total_chars = 0
        skipped = 0

        for f in files_data:
            path = f["path"]
            rel_path = path[len(folder_name) + 1:] if path.startswith(folder_name + "/") else path

            parts = rel_path.split("/")
            if any(should_skip_dir(p) for p in parts[:-1]):
                skipped += 1
                continue
            if should_skip_file(rel_path):
                skipped += 1
                continue

            content_bytes = f["content"]
            if len(content_bytes) > MAX_FILE_SIZE:
                skipped += 1
                continue
            try:
                content = content_bytes.decode("utf-8")
            except (UnicodeDecodeError, AttributeError):
                skipped += 1
                continue
            if total_chars + len(content) > MAX_TOTAL_CHARS:
                skipped += 1
                continue

            is_priority = os.path.basename(rel_path) in PRIORITY_FILES
            lang = detect_language(rel_path)
            language_stats[lang] = language_stats.get(lang, 0) + 1

            processed.append({
                "path": rel_path, "content": content,
                "size": len(content), "is_priority": is_priority,
            })
            total_chars += len(content)

        processed.sort(key=lambda x: (not x["is_priority"], x["size"]))

        sections = ["# Project: {}".format(folder_name), "Files included: {} ({} skipped)".format(len(processed), skipped)]
        if language_stats:
            top = sorted(language_stats.items(), key=lambda x: -x[1])[:8]
            sections.append("Languages: {}".format(", ".join("{}: {}".format(l, c) for l, c in top)))

        priority = [f for f in processed if f["is_priority"]]
        regular = [f for f in processed if not f["is_priority"]]
        for f in priority + regular:
            ext = os.path.splitext(f["path"])[1].lstrip(".")
            sections.append('\n## {}\n```{}\n{}\n```'.format(f["path"], ext, f["content"]))

        repo_text = "\n".join(sections)
        repo_data = {
            "name": folder_name, "url": "upload://" + folder_name,
            "tree": "", "total_chars": total_chars,
            "file_count": len(processed), "skipped": skipped,
            "languages": dict(sorted(language_stats.items(), key=lambda x: -x[1])[:10]),
        }

        repo_entry = {
            "status": "ready", "message": "Ready",
            "data": repo_data, "files": processed, "text": repo_text,
        }
        state.repos.set(repo_id, repo_entry)
        db_async.sync_cache_repo_to_db(repo_id, repo_entry)
        db_async.sync_update_job(repo_id, status="ready", message="Ready",
                           result=json.dumps(repo_data))
    except Exception as e:
        logger.exception("Upload ingest failed")
        db_async.sync_update_job(repo_id, status="error", message=str(e))


@router.post("/api/repo")
async def add_repo(request: Request):
    if await check_rate_limit(request, "repo"):
        return JSONResponse({"error": "Rate limit exceeded (5 repos/hour). Set REPOLM_API_KEY to bypass."}, 429)
    body = await request.json()
    url = body.get("url", "").strip().rstrip("/")
    if not url:
        return JSONResponse({"error": "URL required"}, 400)

    # Accept owner/repo shorthand
    import re
    shorthand = re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', url)
    if shorthand:
        url = "https://github.com/" + url
    elif not url.startswith("http"):
        return JSONResponse({"error": "Please provide a valid GitHub URL (e.g. https://github.com/owner/repo)"}, 400)

    # Strict validation: must be github.com/owner/repo (optionally with trailing path)
    gh_pattern = re.compile(
        r'^https?://(www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(/.*)?$'
    )
    if not gh_pattern.match(url):
        return JSONResponse({"error": "Only GitHub repository URLs are supported (e.g. https://github.com/owner/repo)"}, 400)

    # Normalize to just owner/repo (strip tree/blob paths, .git suffix, query strings)
    url = url.split("?")[0].split("#")[0]
    url = re.sub(r'\.git$', '', url)
    url = re.sub(r'/(tree|blob|pull|issues|compare|commit|releases|actions|wiki)/.*$', '', url)

    # Check URL-based cache first (skip re-clone for recently ingested repos)
    cached_id = await db_async.find_cached_repo_by_url(url)
    if cached_id:
        cached_repo = await db_async.get_repo_with_fallback(cached_id)
        if cached_repo and cached_repo.get("status") == "ready":
            logger.info("Cache hit for %s -> %s", url, cached_id)
            return {"repo_id": cached_id, "token_cost": 0, "cached": True}

    # Per-IP ingest limit
    ip = request.client.host if request.client else "unknown"
    ip_ctx = acquire_ingest(ip)
    if not ip_ctx:
        return JSONResponse({"error": "Too many concurrent ingestions. Please wait."}, 429)

    user = await get_current_user(request)
    cost = TOKEN_COSTS["ingest"]
    if user:
        balance = await db_async.get_token_balance(user["id"])
        if balance < cost:
            ip_ctx.release()
            return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
        await db_async.spend_tokens(user["id"], cost, "Ingest repo")

    repo_id = str(uuid.uuid4())[:8]
    await db_async.create_job(repo_id, kind="ingest", repo_id=repo_id)
    state.repos.set(repo_id, {"status": "queued", "message": "Starting...", "files": [], "text": "", "data": {}})

    def _ingest_with_release():
        try:
            run_ingest(repo_id, url)
        finally:
            ip_ctx.release()

    status, queue_pos = ingest_queue.submit(repo_id, _ingest_with_release)
    if status == "rejected":
        ip_ctx.release()
        return JSONResponse({"error": "Server busy, try again in a moment"}, 503)

    result = {"repo_id": repo_id, "token_cost": cost}
    if status == "queued":
        result["queued"] = True
        result["queue_position"] = queue_pos
    return result


@router.post("/api/upload")
async def upload_folder(request: Request):
    """Handle folder upload via multipart form."""
    if await check_rate_limit(request, "repo"):
        return JSONResponse({"error": "Rate limit exceeded."}, 429)

    form = await request.form()
    files_data = []
    for key in form:
        upload = form[key]
        if hasattr(upload, 'read'):
            content = await upload.read()
            path = upload.filename or key
            files_data.append({"path": path, "content": content})

    if not files_data:
        return JSONResponse({"error": "No files uploaded"}, 400)

    user = await get_current_user(request)
    cost = TOKEN_COSTS["ingest"]
    if user:
        balance = await db_async.get_token_balance(user["id"])
        if balance < cost:
            return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
        await db_async.spend_tokens(user["id"], cost, "Upload folder")

    repo_id = str(uuid.uuid4())[:8]
    await db_async.create_job(repo_id, kind="upload", repo_id=repo_id)
    state.repos.set(repo_id, {"status": "queued", "message": "Processing upload...", "files": [], "text": "", "data": {}})

    status, queue_pos = ingest_queue.submit(repo_id, run_upload_ingest, repo_id, files_data)
    if status == "rejected":
        return JSONResponse({"error": "Server busy, try again in a moment"}, 503)

    result = {"repo_id": repo_id, "token_cost": cost}
    if status == "queued":
        result["queued"] = True
        result["queue_position"] = queue_pos
    return result


@router.get("/api/repo/{repo_id}")
async def get_repo(repo_id: str):
    # Try memory + DB fallback
    repo = await db_async.get_repo_with_fallback(repo_id)
    if repo:
        result = {"status": repo["status"], "message": repo["message"], "data": repo.get("data", {}), "file_count": len(repo.get("files", []))}
        pos = ingest_queue.get_position(repo_id)
        if pos is not None:
            result["queue_position"] = pos
        return result
    # Fallback to jobs DB
    job = await db_async.get_job(repo_id)
    if not job:
        return JSONResponse({"error": "Not found"}, 404)
    data = {}
    if job["result"]:
        try:
            data = json.loads(job["result"])
        except Exception:
            pass
    return {"status": job["status"], "message": job["message"], "data": data, "file_count": data.get("file_count", 0)}


@router.get("/api/repo/{repo_id}/files")
async def get_files(repo_id: str):
    repo = await db_async.get_repo_with_fallback(repo_id)
    if not repo:
        return JSONResponse({"error": "Not found"}, 404)
    return [{"path": f["path"], "size": f["size"], "is_priority": f["is_priority"]} for f in repo.get("files", [])]


@router.get("/api/repo/{repo_id}/file")
async def get_file(repo_id: str, path: str):
    repo = await db_async.get_repo_with_fallback(repo_id)
    if not repo:
        return JSONResponse({"error": "Not found"}, 404)
    for f in repo.get("files", []):
        if f["path"] == path:
            return {"path": f["path"], "content": f["content"], "size": f["size"]}
    return JSONResponse({"error": "File not found"}, 404)


# ── Persistence Routes (authenticated) ──

@router.get("/api/my/repos")
async def my_repos(request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    repos_list = await db_async.get_user_repos(user["id"])
    for r in repos_list:
        r["languages"] = json.loads(r["languages"]) if r.get("languages") else {}
    return repos_list


@router.post("/api/my/repos/{repo_id}/save")
async def save_repo_to_account(repo_id: str, request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    repo = await db_async.get_repo_with_fallback(repo_id)
    if not repo or repo["status"] != "ready":
        return JSONResponse({"error": "Repo not ready"}, 400)
    data = repo["data"]
    file_index = [{"path": f["path"], "size": f["size"], "is_priority": f["is_priority"]} for f in repo["files"]]
    db_id = await db_async.save_repo(
        user_id=user["id"], url=data["url"], name=data["name"], tree=data.get("tree", ""),
        file_count=data["file_count"], total_chars=data["total_chars"],
        languages=data["languages"], repo_text=repo["text"], file_index=file_index
    )
    # Also ensure cold cache has the full files for reload
    await db_async.cache_repo_to_db(repo_id, repo)
    return {"db_id": db_id, "cache_repo_id": repo_id}


@router.get("/api/my/repos/{db_id}")
async def get_saved_repo(db_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    saved = await db_async.get_repo(db_id, user["id"])
    if not saved:
        return JSONResponse({"error": "Not found"}, 404)
    # Try to restore full files from cold cache
    files = []
    url = saved.get("url", "")

    # Method 1: find by URL in cold cache
    if url:
        cached_id = await find_cached_repo_any(url)
        if cached_id:
            cached = await db_async.get_repo_with_fallback(cached_id)
            if cached and cached.get("files"):
                files = cached["files"]

    # Method 2: reconstruct files from repo_text (always available)
    if not files and saved.get("repo_text"):
        files = _reconstruct_files_from_text(saved["repo_text"], saved.get("file_index", []))

    repo_id = str(uuid.uuid4())[:8]
    repo_entry = {
        "status": "ready", "message": "Ready",
        "data": {"name": saved["name"], "url": url, "tree": saved.get("tree", ""),
                 "total_chars": saved["total_chars"], "file_count": saved["file_count"],
                 "skipped": 0, "languages": saved["languages"]},
        "files": files,
        "text": saved.get("repo_text", ""),
    }
    state.repos.set(repo_id, repo_entry)
    return {"repo_id": repo_id, "data": repo_entry["data"], "file_index": saved["file_index"], "has_files": len(files) > 0}


@router.delete("/api/my/repos/{db_id}")
async def delete_saved_repo(db_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    await db_async.delete_repo(db_id, user["id"])
    return {"ok": True}


@router.post("/api/my/repos/{db_id}/generated")
async def save_generated_content(db_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    body = await request.json()
    await db_async.save_generated(db_id, body["kind"], body["depth"], body["expertise"], body["content"])
    return {"ok": True}


@router.get("/api/my/repos/{db_id}/generated")
async def get_generated_content(db_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    return await db_async.get_generated(db_id)


@router.post("/api/my/repos/{db_id}/chat")
async def save_chat_message(db_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    body = await request.json()
    await db_async.save_chat(db_id, body["role"], body["message"], body.get("selection"), body.get("file_path"))
    return {"ok": True}


@router.get("/api/my/repos/{db_id}/chats")
async def get_chat_history(db_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    return await db_async.get_chats(db_id)
