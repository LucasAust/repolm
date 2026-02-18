"""
RepoLM — Web UI v3 (Landing + App + Audio + Rate Limiting)
"""

import os
import re
import uuid
import json
import time
import asyncio
import threading
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from ingest import ingest_repo, repo_to_text, RepoData
from summarize import call_llm
import db as database
from auth import router as auth_router, get_current_user
from payments import router as payments_router

app = FastAPI(title="RepoLM")

# ── Token costs ──
TOKEN_COSTS = {"ingest": 5, "overview": 10, "chat": 2, "slides": 15, "podcast": 20, "audio": 30, "immersive": 3}
ADSENSE_CLIENT_ID = os.environ.get("ADSENSE_CLIENT_ID", "")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_router)
app.include_router(payments_router)

# ── Store ──
repos = {}   # repo_id -> { data, text, files, status, message }
jobs = {}    # job_id -> { status, message, result }

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
EXAMPLES_DIR = os.path.join(OUTPUT_DIR, "examples")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(EXAMPLES_DIR, exist_ok=True)

# ── Rate Limiting ──
RATE_LIMITS = {
    "repo": {"max": 5, "window": 3600},
    "chat": {"max": 20, "window": 3600},
}
rate_store = defaultdict(list)  # "type:ip" -> [timestamps]
API_KEY = os.environ.get("REPOLM_API_KEY")

def is_pro_user(request: Request) -> bool:
    """Check if the current user has an active Pro subscription."""
    user = get_current_user(request)
    if not user:
        return False
    sub = database.get_subscription(user["id"])
    if not sub:
        return False
    return sub.get("plan") == "pro" and sub.get("subscription_status") == "active"


def check_rate_limit(request: Request, action: str) -> bool:
    """Returns True if rate limited."""
    if API_KEY:
        req_key = request.headers.get("x-api-key", "")
        if req_key == API_KEY:
            return False
    ip = request.client.host
    key = f"{action}:{ip}"
    now = time.time()
    window = RATE_LIMITS[action]["window"]
    rate_store[key] = [t for t in rate_store[key] if now - t < window]
    if len(rate_store[key]) >= RATE_LIMITS[action]["max"]:
        return True
    rate_store[key].append(now)
    return False

# ── Prompts ──
DEPTH_PROMPTS = {
    "high-level": "Focus on architecture, design decisions, and the big picture. Skip implementation details.",
    "low-level": "Go deep into implementation details, algorithms, data structures, and code paths.",
}

EXPERTISE_PROMPTS = {
    "beginner": "Explain like I'm a first-year CS student. Use simple analogies, define all technical terms, and go step by step. Assume I know basic programming but nothing advanced. Use MINIMAL code snippets — only when absolutely necessary, and keep them very short (1-3 lines max). Prefer plain English explanations, diagrams, and analogies over code.",
    "amateur": "Explain for someone who codes regularly but may not know this domain well. You can use technical terms but explain domain-specific concepts. Include a MODERATE amount of code snippets — show key functions and patterns but don't dump entire files. Balance code with explanation, roughly 30-40% code.",
    "expert": "Explain for an experienced software engineer. Be concise, use proper terminology, skip basics. Focus on interesting design decisions and tradeoffs. Be CODE-HEAVY — show extensive code snippets, full function implementations, type signatures, and internal logic. Let the code speak. 60-70% of your response should be actual code with brief annotations.",
}

OVERVIEW_SYSTEM = """You are RepoLM, an expert code educator. You analyze GitHub repositories and explain them clearly.
You have access to the full repository source code. When answering, reference specific files and code when relevant.
{depth}
{expertise}
Be thorough but engaging. Use examples from the actual code."""

CHAT_SYSTEM = """You are RepoLM, an AI that helps people understand GitHub repositories.
You have the full source code loaded. Answer questions about the codebase accurately,
referencing specific files, functions, and code patterns.
{depth}
{expertise}
If the user highlights specific code or text, focus your explanation on that selection.
Be conversational and helpful. Use code snippets in your answers when it helps."""

PODCAST_SYSTEM = """You are a scriptwriter for a technical podcast called "RepoLM".
Two hosts — Alex (enthusiastic, asks great questions) and Sam (deep technical knowledge, amazing analogies) — break down a codebase.

{depth}
{expertise}

Write a natural, engaging conversation (2000-3000 words, ~12 min).
Rules:
- Real conversation, not a lecture. Include laughs, surprise, building on each other's points.
- Alex asks the questions a learner would ask
- Sam explains with analogies and code examples
- Start with a hook. End with key takeaways.

Format: ALEX: [text]  /  SAM: [text]"""

SLIDES_SYSTEM = """Create a presentation deck (12-20 slides) about this repository.
{depth}
{expertise}

Format each slide as:
---
# Slide Title
- Point (3-5 per slide max)
```lang
short code snippet if helpful
```
**Key Takeaway:** one sentence
---"""

SELECTION_SYSTEM = """You are RepoLM. The user has highlighted a section of code or text and is asking about it.
Here is the full context of the file they're viewing, and their highlighted selection.
{depth}
{expertise}
Explain the highlighted section clearly. Reference how it connects to the broader codebase when relevant."""


def get_system_prompt(template, depth="high-level", expertise="amateur"):
    return template.format(
        depth=DEPTH_PROMPTS.get(depth, ""),
        expertise=EXPERTISE_PROMPTS.get(expertise, ""),
    )


def run_ingest(repo_id, url):
    job = repos[repo_id]
    try:
        job["status"] = "ingesting"
        job["message"] = "Cloning repository..."
        data = ingest_repo(url)
        job["status"] = "processing"
        job["message"] = "Processing files..."
        text = repo_to_text(data)
        file_list = [{"path": f.path, "content": f.content, "size": f.size, "is_priority": f.is_priority} for f in data.files]
        job["data"] = {
            "name": data.name, "url": url, "tree": data.tree,
            "total_chars": data.total_chars, "file_count": len(data.files),
            "skipped": data.skipped_count,
            "languages": dict(sorted(data.language_stats.items(), key=lambda x: -x[1])[:10]),
        }
        job["files"] = file_list
        job["text"] = text
        job["status"] = "ready"
        job["message"] = "Ready"
    except Exception as e:
        job["status"] = "error"
        job["message"] = str(e)


def run_generate(job_id, repo_id, kind, depth, expertise):
    job = jobs[job_id]
    repo = repos.get(repo_id)
    if not repo or repo["status"] != "ready":
        job["status"] = "error"
        job["message"] = "Repo not ready"
        return
    try:
        text = repo["text"]
        if len(text) > 200_000:
            text = text[:200_000] + "\n\n[... truncated ...]"
        templates = {"overview": OVERVIEW_SYSTEM, "podcast": PODCAST_SYSTEM, "slides": SLIDES_SYSTEM}
        if kind not in templates:
            job["status"] = "error"
            job["message"] = f"Unknown kind: {kind}"
            return
        system = get_system_prompt(templates[kind], depth, expertise)
        prompts = {
            "overview": f"Analyze this repository and provide a comprehensive overview:\n\n{text}",
            "podcast": f"Write a podcast script about this repository:\n\n{text}",
            "slides": f"Create a slide deck about this repository:\n\n{text}",
        }
        job["status"] = "generating"
        job["message"] = f"Generating {kind}..."
        result = call_llm(system, prompts[kind])
        job["result"] = result
        job["status"] = "done"
        job["message"] = "Done"
    except Exception as e:
        job["status"] = "error"
        job["message"] = str(e)


# ── Podcast Audio ──
EDGE_VOICES = {"ALEX": "en-US-GuyNeural", "SAM": "en-US-JennyNeural"}

def parse_podcast_script(text):
    pattern = r"(ALEX|SAM):\s*(.+?)(?=\n(?:ALEX|SAM):|$)"
    matches = re.findall(pattern, text, re.DOTALL)
    lines = []
    for speaker, dialogue in matches:
        dialogue = dialogue.strip()
        dialogue = re.sub(r'\*\*(.+?)\*\*', r'\1', dialogue)
        dialogue = re.sub(r'`(.+?)`', r'\1', dialogue)
        if dialogue:
            lines.append((speaker, dialogue))
    return lines

async def _generate_audio_segments(lines, audio_dir):
    import edge_tts
    segment_paths = []
    for i, (speaker, text) in enumerate(lines):
        path = os.path.join(audio_dir, f"{i:03d}_{speaker.lower()}.mp3")
        voice = EDGE_VOICES.get(speaker, "en-US-GuyNeural")
        comm = edge_tts.Communicate(text, voice)
        await comm.save(path)
        segment_paths.append(path)
    return segment_paths

def generate_podcast_audio(script_text, audio_id):
    """Generate podcast audio, return path to mp3."""
    lines = parse_podcast_script(script_text)
    if not lines:
        return None
    audio_dir = os.path.join(OUTPUT_DIR, f"audio_{audio_id}")
    os.makedirs(audio_dir, exist_ok=True)
    
    loop = asyncio.new_event_loop()
    segment_paths = loop.run_until_complete(_generate_audio_segments(lines, audio_dir))
    loop.close()
    
    # Concatenate with ffmpeg if available, otherwise return first segment
    final_path = os.path.join(OUTPUT_DIR, f"podcast_{audio_id}.mp3")
    concat_file = os.path.join(audio_dir, "concat.txt")
    with open(concat_file, "w") as f:
        for sp in segment_paths:
            f.write(f"file '{os.path.abspath(sp)}'\n")
    
    import subprocess
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", final_path],
        capture_output=True
    )
    if result.returncode != 0:
        # ffmpeg not available - just use concatenated raw bytes
        with open(final_path, "wb") as out:
            for sp in segment_paths:
                with open(sp, "rb") as seg:
                    out.write(seg.read())
    return final_path

audio_jobs = {}  # audio_id -> { status, path }

def run_audio_gen(audio_id, script_text):
    audio_jobs[audio_id]["status"] = "generating"
    try:
        path = generate_podcast_audio(script_text, audio_id)
        audio_jobs[audio_id]["status"] = "done"
        audio_jobs[audio_id]["path"] = path
    except Exception as e:
        audio_jobs[audio_id]["status"] = "error"
        audio_jobs[audio_id]["message"] = str(e)


# ── Routes ──

@app.get("/", response_class=HTMLResponse)
async def landing():
    return LANDING_PAGE

@app.get("/app", response_class=HTMLResponse)
async def app_page():
    return APP_PAGE.replace("__ADSENSE_CLIENT_ID__", ADSENSE_CLIENT_ID)

@app.post("/api/repo")
async def add_repo(request: Request):
    if check_rate_limit(request, "repo"):
        return JSONResponse({"error": "Rate limit exceeded (5 repos/hour). Set REPOLM_API_KEY to bypass."}, 429)
    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "URL required"}, 400)
    if not url.startswith("http"):
        url = "https://github.com/" + url
    # Basic URL validation
    if "github.com" not in url and "gitlab.com" not in url:
        return JSONResponse({"error": "Please provide a valid GitHub or GitLab URL"}, 400)
    # Check token balance for authenticated users
    user = get_current_user(request)
    cost = TOKEN_COSTS["ingest"]
    if user:
        balance = database.get_token_balance(user["id"])
        if balance < cost:
            return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
        database.spend_tokens(user["id"], cost, "Ingest repo")

    repo_id = str(uuid.uuid4())[:8]
    repos[repo_id] = {"status": "queued", "message": "Starting...", "files": [], "text": "", "data": {}}
    threading.Thread(target=run_ingest, args=(repo_id, url), daemon=True).start()
    return {"repo_id": repo_id, "token_cost": cost}

@app.post("/api/upload")
async def upload_folder(request: Request):
    """Handle folder upload via multipart form. Files come with webkitRelativePath."""
    if check_rate_limit(request, "repo"):
        return JSONResponse({"error": "Rate limit exceeded."}, 429)
    
    form = await request.form()
    files_data = []
    for key in form:
        upload = form[key]
        if hasattr(upload, 'read'):
            content = await upload.read()
            # filename contains the relative path from webkitdirectory
            path = upload.filename or key
            files_data.append({"path": path, "content": content})
    
    if not files_data:
        return JSONResponse({"error": "No files uploaded"}, 400)

    # Check token balance
    user = get_current_user(request)
    cost = TOKEN_COSTS["ingest"]
    if user:
        balance = database.get_token_balance(user["id"])
        if balance < cost:
            return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
        database.spend_tokens(user["id"], cost, "Upload folder")

    repo_id = str(uuid.uuid4())[:8]
    repos[repo_id] = {"status": "queued", "message": "Processing upload...", "files": [], "text": "", "data": {}}
    threading.Thread(target=run_upload_ingest, args=(repo_id, files_data), daemon=True).start()
    return {"repo_id": repo_id, "token_cost": cost}


def run_upload_ingest(repo_id, files_data):
    """Process uploaded folder files."""
    from ingest import should_skip_file, should_skip_dir, build_tree, detect_language, MAX_FILE_SIZE, MAX_TOTAL_CHARS, PRIORITY_FILES, RepoFile, RepoData
    job = repos[repo_id]
    try:
        job["status"] = "processing"
        job["message"] = "Processing uploaded files..."

        # Determine folder name from common prefix
        paths = [f["path"] for f in files_data]
        if paths and "/" in paths[0]:
            folder_name = paths[0].split("/")[0]
        else:
            folder_name = "uploaded-project"

        # Filter and process files
        processed = []
        language_stats = {}
        total_chars = 0
        skipped = 0

        for f in files_data:
            path = f["path"]
            # Strip leading folder name if all files share it
            if path.startswith(folder_name + "/"):
                rel_path = path[len(folder_name) + 1:]
            else:
                rel_path = path

            # Skip filters
            parts = rel_path.split("/")
            if any(should_skip_dir(p) for p in parts[:-1]):
                skipped += 1
                continue
            if should_skip_file(rel_path):
                skipped += 1
                continue

            # Try decode as text
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
                "path": rel_path,
                "content": content,
                "size": len(content),
                "is_priority": is_priority,
            })
            total_chars += len(content)

        # Sort: priority first, then by size
        processed.sort(key=lambda x: (not x["is_priority"], x["size"]))

        # Build text representation
        sections = [f"# Project: {folder_name}", f"Files included: {len(processed)} ({skipped} skipped)"]
        if language_stats:
            top = sorted(language_stats.items(), key=lambda x: -x[1])[:8]
            sections.append(f"Languages: {', '.join(f'{l}: {c}' for l, c in top)}")
        
        priority = [f for f in processed if f["is_priority"]]
        regular = [f for f in processed if not f["is_priority"]]
        for f in priority + regular:
            ext = os.path.splitext(f["path"])[1].lstrip(".")
            sections.append(f'\n## {f["path"]}\n```{ext}\n{f["content"]}\n```')

        repo_text = "\n".join(sections)

        job["data"] = {
            "name": folder_name, "url": "upload://" + folder_name,
            "tree": "", "total_chars": total_chars,
            "file_count": len(processed), "skipped": skipped,
            "languages": dict(sorted(language_stats.items(), key=lambda x: -x[1])[:10]),
        }
        job["files"] = processed
        job["text"] = repo_text
        job["status"] = "ready"
        job["message"] = "Ready"
    except Exception as e:
        job["status"] = "error"
        job["message"] = str(e)


@app.get("/api/repo/{repo_id}")
async def get_repo(repo_id: str):
    repo = repos.get(repo_id)
    if not repo:
        return JSONResponse({"error": "Not found"}, 404)
    return {"status": repo["status"], "message": repo["message"], "data": repo.get("data", {}), "file_count": len(repo.get("files", []))}

@app.get("/api/repo/{repo_id}/files")
async def get_files(repo_id: str):
    repo = repos.get(repo_id)
    if not repo:
        return JSONResponse({"error": "Not found"}, 404)
    return [{"path": f["path"], "size": f["size"], "is_priority": f["is_priority"]} for f in repo.get("files", [])]

@app.get("/api/repo/{repo_id}/file")
async def get_file(repo_id: str, path: str):
    repo = repos.get(repo_id)
    if not repo:
        return JSONResponse({"error": "Not found"}, 404)
    for f in repo.get("files", []):
        if f["path"] == path:
            return {"path": f["path"], "content": f["content"], "size": f["size"]}
    return JSONResponse({"error": "File not found"}, 404)

@app.post("/api/repo/{repo_id}/chat")
async def chat(repo_id: str, request: Request):
    if check_rate_limit(request, "chat"):
        return JSONResponse({"error": "Rate limit exceeded (20 chats/hour)."}, 429)
    body = await request.json()
    message = body.get("message", "")
    depth = body.get("depth", "high-level")
    expertise = body.get("expertise", "amateur")
    selection = body.get("selection")
    file_context = body.get("file_path")
    repo = repos.get(repo_id)
    if not repo or repo["status"] != "ready":
        return JSONResponse({"error": "Repo not ready"}, 400)
    # Token check
    user = get_current_user(request)
    is_immersive = bool(selection and file_context)
    cost = TOKEN_COSTS["immersive"] if is_immersive else TOKEN_COSTS["chat"]
    if user:
        balance = database.get_token_balance(user["id"])
        if balance < cost:
            return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
    if selection and file_context:
        system = get_system_prompt(SELECTION_SYSTEM, depth, expertise)
        file_content = ""
        for f in repo.get("files", []):
            if f["path"] == file_context:
                file_content = f["content"]
                break
        prompt = f"File: {file_context}\n```\n{file_content}\n```\n\nHighlighted selection:\n```\n{selection}\n```\n\nUser question: {message}"
    else:
        system = get_system_prompt(CHAT_SYSTEM, depth, expertise)
        text = repo["text"]
        if len(text) > 150_000:
            text = text[:150_000] + "\n\n[... truncated ...]"
        prompt = f"Repository context:\n{text}\n\nUser question: {message}"
    try:
        result = call_llm(system, prompt)
        if user:
            database.spend_tokens(user["id"], cost, "Immersive question" if is_immersive else "Chat message")
        new_balance = database.get_token_balance(user["id"]) if user else None
        return {"response": result, "token_cost": cost, "balance": new_balance}
    except Exception as e:
        return JSONResponse({"error": f"AI generation failed: {str(e)}"}, 500)

@app.post("/api/repo/{repo_id}/generate")
async def generate(repo_id: str, request: Request):
    body = await request.json()
    kind = body.get("kind", "overview")
    depth = body.get("depth", "high-level")
    expertise = body.get("expertise", "amateur")
    # Token check
    cost = TOKEN_COSTS.get(kind, 10)
    user = get_current_user(request)
    if user:
        balance = database.get_token_balance(user["id"])
        if balance < cost:
            return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
        database.spend_tokens(user["id"], cost, f"Generate {kind}")
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "queued", "message": "Starting...", "result": None}
    threading.Thread(target=run_generate, args=(job_id, repo_id, kind, depth, expertise), daemon=True).start()
    return {"job_id": job_id}

@app.get("/api/job/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Not found"}, 404)
    return job

@app.post("/api/podcast-audio")
async def podcast_audio(request: Request):
    user = get_current_user(request)
    cost = TOKEN_COSTS["audio"]
    if user:
        balance = database.get_token_balance(user["id"])
        if balance < cost:
            return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
        database.spend_tokens(user["id"], cost, "Podcast audio generation")
    body = await request.json()
    script = body.get("script", "")
    if not script:
        return JSONResponse({"error": "Script required"}, 400)
    audio_id = str(uuid.uuid4())[:8]
    audio_jobs[audio_id] = {"status": "queued", "path": None, "message": ""}
    threading.Thread(target=run_audio_gen, args=(audio_id, script), daemon=True).start()
    return {"audio_id": audio_id}

@app.get("/api/podcast-audio/{audio_id}")
async def get_podcast_audio(audio_id: str):
    job = audio_jobs.get(audio_id)
    if not job:
        return JSONResponse({"error": "Not found"}, 404)
    if job["status"] == "done" and job.get("path"):
        return {"status": "done", "url": f"/api/podcast-audio/{audio_id}/file"}
    return {"status": job["status"], "message": job.get("message", "")}

@app.get("/api/podcast-audio/{audio_id}/file")
async def get_podcast_audio_file(audio_id: str):
    job = audio_jobs.get(audio_id)
    if not job or not job.get("path"):
        return JSONResponse({"error": "Not found"}, 404)
    return FileResponse(job["path"], media_type="audio/mpeg", filename="podcast.mp3")

@app.get("/api/examples")
async def get_examples():
    examples = []
    for fname in sorted(os.listdir(EXAMPLES_DIR)):
        if fname.endswith(".json"):
            with open(os.path.join(EXAMPLES_DIR, fname)) as f:
                examples.append(json.load(f))
    return examples

@app.get("/api/examples/{slug}")
async def get_example(slug: str):
    path = os.path.join(EXAMPLES_DIR, f"{slug}.json")
    if not os.path.exists(path):
        return JSONResponse({"error": "Not found"}, 404)
    with open(path) as f:
        return json.load(f)


# ── Persistence Routes (authenticated) ──

@app.get("/api/my/repos")
async def my_repos(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    repos_list = database.get_user_repos(user["id"])
    for r in repos_list:
        r["languages"] = json.loads(r["languages"]) if r.get("languages") else {}
    return repos_list

@app.post("/api/my/repos/{repo_id}/save")
async def save_repo_to_account(repo_id: str, request: Request):
    """Save a temporary in-memory repo to the user's account."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    repo = repos.get(repo_id)
    if not repo or repo["status"] != "ready":
        return JSONResponse({"error": "Repo not ready"}, 400)
    data = repo["data"]
    file_index = [{"path": f["path"], "size": f["size"], "is_priority": f["is_priority"]} for f in repo["files"]]
    db_id = database.save_repo(
        user_id=user["id"], url=data["url"], name=data["name"], tree=data.get("tree", ""),
        file_count=data["file_count"], total_chars=data["total_chars"],
        languages=data["languages"], repo_text=repo["text"], file_index=file_index
    )
    return {"db_id": db_id}

@app.get("/api/my/repos/{db_id}")
async def get_saved_repo(db_id: int, request: Request):
    """Load a saved repo back into memory for use."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    saved = database.get_repo(db_id, user["id"])
    if not saved:
        return JSONResponse({"error": "Not found"}, 404)
    # Re-hydrate into in-memory store
    repo_id = str(uuid.uuid4())[:8]
    repos[repo_id] = {
        "status": "ready", "message": "Ready",
        "data": {"name": saved["name"], "url": saved["url"], "tree": saved.get("tree", ""),
                 "total_chars": saved["total_chars"], "file_count": saved["file_count"],
                 "skipped": 0, "languages": saved["languages"]},
        "files": [],  # files need re-clone for content (we only store index)
        "text": saved.get("repo_text", ""),
    }
    return {"repo_id": repo_id, "data": repos[repo_id]["data"], "file_index": saved["file_index"]}

@app.delete("/api/my/repos/{db_id}")
async def delete_saved_repo(db_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    database.delete_repo(db_id, user["id"])
    return {"ok": True}

@app.post("/api/my/repos/{db_id}/generated")
async def save_generated_content(db_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    body = await request.json()
    database.save_generated(db_id, body["kind"], body["depth"], body["expertise"], body["content"])
    return {"ok": True}

@app.get("/api/my/repos/{db_id}/generated")
async def get_generated_content(db_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    return database.get_generated(db_id)

@app.post("/api/my/repos/{db_id}/chat")
async def save_chat_message(db_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    body = await request.json()
    database.save_chat(db_id, body["role"], body["message"], body.get("selection"), body.get("file_path"))
    return {"ok": True}

@app.get("/api/my/repos/{db_id}/chats")
async def get_chat_history(db_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    return database.get_chats(db_id)


# ── HTML: Landing Page ──
LANDING_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RepoLM — Learn Any Codebase</title>
<meta name="description" content="Paste a GitHub repo. Get overviews, podcasts, and slides. Learn any codebase from beginner to expert.">
<meta property="og:title" content="RepoLM — Learn Any Codebase">
<meta property="og:description" content="AI-powered code education. Paste a repo, get overviews, podcasts, slides, and interactive chat.">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="RepoLM — Learn Any Codebase">
<meta name="twitter:description" content="Paste a GitHub repo. Get overviews, podcasts, and slides. From beginner to expert.">
<script src="https://cdn.tailwindcss.com"></script>
<style>
body{background:#09090b;color:#e5e7eb;font-family:system-ui,-apple-system,sans-serif}
.glow{box-shadow:0 0 60px rgba(139,92,246,0.15)}
.card-hover{transition:all 0.2s}
.card-hover:hover{transform:translateY(-2px);border-color:#6d28d9}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}
.float{animation:float 3s ease-in-out infinite}
.gradient-text{background:linear-gradient(135deg,#a78bfa,#c084fc,#e879f9);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
</style>
</head>
<body>
<div class="min-h-screen">
<!-- Nav -->
<nav class="max-w-6xl mx-auto px-6 py-5 flex items-center justify-between">
    <span class="text-xl font-bold"><span class="text-purple-400">Repo</span>LM</span>
    <a href="/app" class="text-sm text-purple-400 hover:text-purple-300 transition">Open App →</a>
</nav>

<!-- Hero -->
<section class="max-w-4xl mx-auto px-6 pt-20 pb-16 text-center">
    <div class="inline-block px-3 py-1 rounded-full bg-purple-900/30 border border-purple-800/50 text-purple-300 text-xs mb-6">✨ AI-powered code education</div>
    <h1 class="text-5xl md:text-7xl font-bold mb-6 leading-tight">
        Learn any codebase<br><span class="gradient-text">in minutes</span>
    </h1>
    <p class="text-xl text-gray-400 mb-10 max-w-2xl mx-auto">
        Paste a GitHub repo. Get overviews, podcast-style explanations, and slide decks.<br>
        From beginner to expert — at your level.
    </p>
    <a href="/app" class="inline-block bg-purple-600 hover:bg-purple-500 text-white font-semibold px-8 py-4 rounded-xl text-lg transition glow">
        Try RepoLM →
    </a>
</section>

<!-- Features -->
<section class="max-w-5xl mx-auto px-6 py-16">
    <div class="grid md:grid-cols-3 gap-6">
        <div class="bg-gray-900/50 border border-gray-800 rounded-2xl p-6 card-hover">
            <div class="text-3xl mb-3">📖</div>
            <h3 class="text-lg font-semibold mb-2">Overview & Slides</h3>
            <p class="text-gray-400 text-sm">Architecture breakdowns, concept maps, and presentation-ready slides — generated from the actual code.</p>
        </div>
        <div class="bg-gray-900/50 border border-gray-800 rounded-2xl p-6 card-hover">
            <div class="text-3xl mb-3">🎙️</div>
            <h3 class="text-lg font-semibold mb-2">Podcast Mode</h3>
            <p class="text-gray-400 text-sm">Two AI hosts break down the codebase like friends geeking out. Listen while you commute.</p>
        </div>
        <div class="bg-gray-900/50 border border-gray-800 rounded-2xl p-6 card-hover">
            <div class="text-3xl mb-3">🔍</div>
            <h3 class="text-lg font-semibold mb-2">Immersive Mode</h3>
            <p class="text-gray-400 text-sm">Highlight any code and ask questions. Like pair programming with an expert who knows the entire codebase.</p>
        </div>
    </div>
</section>

<!-- Levels -->
<section class="max-w-4xl mx-auto px-6 py-12 text-center">
    <h2 class="text-2xl font-bold mb-3">At your level</h2>
    <p class="text-gray-400 mb-8">Whether you're a CS student or a senior engineer, RepoLM adapts.</p>
    <div class="flex justify-center gap-4 flex-wrap">
        <div class="bg-gray-900 border border-gray-800 rounded-xl px-6 py-3">
            <div class="text-purple-400 font-medium">🌱 Beginner</div>
            <div class="text-xs text-gray-500 mt-1">Simple analogies, step-by-step</div>
        </div>
        <div class="bg-gray-900 border border-purple-700 rounded-xl px-6 py-3 glow">
            <div class="text-purple-300 font-medium">🔧 Amateur</div>
            <div class="text-xs text-gray-500 mt-1">Balanced code & explanation</div>
        </div>
        <div class="bg-gray-900 border border-gray-800 rounded-xl px-6 py-3">
            <div class="text-purple-400 font-medium">⚡ Expert</div>
            <div class="text-xs text-gray-500 mt-1">Code-heavy, skip the basics</div>
        </div>
    </div>
</section>

<!-- Ad on landing page -->
<section class="max-w-5xl mx-auto px-6 py-2">
    <div class="ad-slot ad-banner flex items-center justify-center" style="background:#111827;border:1px solid #1f2937;border-radius:8px;height:90px;text-align:center">
        <span style="position:absolute;top:2px;right:6px;font-size:9px;color:#4b5563">Ad</span>
        <ins class="adsbygoogle" style="display:inline-block;width:728px;height:90px" data-ad-client="ca-pub-XXXX" data-ad-slot="LANDING_AD_SLOT"></ins>
        <script>(adsbygoogle = window.adsbygoogle || []).push({});</script>
    </div>
</section>

<!-- Examples -->
<section class="max-w-5xl mx-auto px-6 py-16" id="examples" x-data="examplesData()" x-init="loadExamples()">
    <h2 class="text-2xl font-bold mb-2 text-center">Try these</h2>
    <p class="text-gray-400 text-center mb-8">Pre-generated — loads instantly</p>
    <div class="grid md:grid-cols-2 gap-6">
        <template x-for="ex in examples" :key="ex.slug">
            <a :href="'/app?example=' + ex.slug" class="bg-gray-900/50 border border-gray-800 rounded-2xl p-6 card-hover block">
                <div class="flex items-center gap-3 mb-3">
                    <div class="text-2xl">📦</div>
                    <div>
                        <h3 class="font-semibold" x-text="ex.name"></h3>
                        <p class="text-xs text-gray-500" x-text="ex.repo"></p>
                    </div>
                </div>
                <p class="text-sm text-gray-400" x-text="ex.description"></p>
                <div class="mt-3 flex gap-2">
                    <template x-for="tag in (ex.tags || [])" :key="tag">
                        <span class="text-[10px] bg-purple-900/30 text-purple-300 px-2 py-0.5 rounded" x-text="tag"></span>
                    </template>
                </div>
            </a>
        </template>
    </div>
</section>

<!-- Footer -->
<footer class="max-w-6xl mx-auto px-6 py-10 border-t border-gray-800 text-center text-gray-600 text-sm">
    <span class="text-purple-400 font-semibold">Repo</span>LM — Powered by Gemini
</footer>
</div>

<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<script>
function examplesData() {
    return {
        examples: [],
        async loadExamples() {
            try {
                const res = await fetch('/api/examples');
                this.examples = await res.json();
            } catch(e) {}
        }
    }
}
</script>
</body>
</html>"""


# ── HTML: App Page ──
APP_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RepoLM</title>
<script src="https://cdn.tailwindcss.com"></script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
[x-cloak]{display:none!important}
*{scrollbar-width:thin;scrollbar-color:#374151 transparent}
.file-tree::-webkit-scrollbar,.chat-area::-webkit-scrollbar,.viewer::-webkit-scrollbar{width:6px}
.file-tree::-webkit-scrollbar-thumb,.chat-area::-webkit-scrollbar-thumb,.viewer::-webkit-scrollbar-thumb{background:#374151;border-radius:3px}
.prose pre{background:#0d1117;border-radius:8px;padding:12px;overflow-x:auto;font-size:13px}
.prose code{font-size:13px}
.prose h1,.prose h2,.prose h3{color:#e5e7eb}
.prose p,.prose li{color:#d1d5db}
.code-viewer .line:hover{background:rgba(139,92,246,0.08)}
::selection{background:rgba(139,92,246,0.4)}
@keyframes fade-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.msg-enter{animation:fade-in 0.2s ease-out}
.spinner{border:2px solid #374151;border-top:2px solid #8b5cf6;border-radius:50%;width:18px;height:18px;animation:spin 0.8s linear infinite;display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes shimmer{0%{background-position:-200% 0}100%{background-position:200% 0}}
.skeleton{background:linear-gradient(90deg,#1f2937 25%,#374151 50%,#1f2937 75%);background-size:200% 100%;animation:shimmer 1.5s infinite;border-radius:4px}
.ad-slot{background:#111827;border:1px solid #1f2937;border-radius:8px;text-align:center;overflow:hidden;position:relative}
.ad-slot .ad-label{position:absolute;top:2px;right:6px;font-size:9px;color:#4b5563;z-index:1}
.ad-banner{height:90px;width:100%}
.ad-sidebar{min-height:250px;width:100%}
.ad-inline{height:60px;width:100%;margin:8px 0}
</style>
<!-- Google AdSense -->
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-XXXX" crossorigin="anonymous"></script>
<script>window.ADSENSE_CLIENT_ID = '__ADSENSE_CLIENT_ID__';</script>
</head>
<body class="bg-gray-950 text-gray-100 h-screen overflow-hidden">

<div x-data="repolm()" x-init="init()" x-cloak class="h-screen flex flex-col">

<!-- Checkout success banner -->
<div x-show="checkoutSuccess" x-transition class="fixed top-0 left-0 right-0 z-50 bg-green-600 text-white text-center py-3 text-sm font-medium">
    🎉 Tokens added! Your balance has been updated.
</div>

<!-- Auth modal -->
<div x-show="showAuthModal" x-transition class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" @click.self="showAuthModal=false">
    <div class="bg-gray-900 border border-gray-700 rounded-2xl p-6 max-w-sm w-full mx-4">
        <div class="flex justify-between items-center mb-4">
            <h3 class="text-lg font-bold" x-text="authMode==='login' ? 'Sign In' : 'Create Account'"></h3>
            <button @click="showAuthModal=false" class="text-gray-500 hover:text-white">✕</button>
        </div>
        <div x-show="authError" class="mb-3 bg-red-900/30 border border-red-700 rounded-lg px-3 py-2 text-red-300 text-xs" x-text="authError"></div>
        <div class="space-y-3">
            <div x-show="authMode==='signup'">
                <label class="text-xs text-gray-400 mb-1 block">Username</label>
                <input x-model="authUsername" type="text" placeholder="your name"
                    class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-purple-500">
            </div>
            <div>
                <label class="text-xs text-gray-400 mb-1 block">Email</label>
                <input x-model="authEmail" type="email" placeholder="you@email.com"
                    @keydown.enter="submitAuth()"
                    class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-purple-500">
            </div>
            <div>
                <label class="text-xs text-gray-400 mb-1 block">Password</label>
                <input x-model="authPassword" type="password" placeholder="••••••"
                    @keydown.enter="submitAuth()"
                    class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-purple-500">
            </div>
            <button @click="submitAuth()" :disabled="authLoading"
                class="w-full bg-purple-600 hover:bg-purple-500 disabled:bg-gray-700 py-2.5 rounded-lg font-medium text-sm transition">
                <span x-show="!authLoading" x-text="authMode==='login' ? 'Sign In' : 'Create Account'"></span>
                <span x-show="authLoading" class="spinner"></span>
            </button>
            <p class="text-center text-xs text-gray-500">
                <span x-show="authMode==='login'">No account? <button @click="authMode='signup';authError=''" class="text-purple-400 hover:text-purple-300">Sign up</button></span>
                <span x-show="authMode==='signup'">Already have an account? <button @click="authMode='login';authError=''" class="text-purple-400 hover:text-purple-300">Sign in</button></span>
            </p>
        </div>
        <p class="text-center text-[10px] text-gray-600 mt-3">🪙 10 free tokens on signup</p>
    </div>
</div>

<!-- Token shop modal -->
<div x-show="showTokenShop" x-transition class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" @click.self="showTokenShop=false">
    <div class="bg-gray-900 border border-gray-700 rounded-2xl p-8 max-w-lg w-full mx-4 shadow-2xl">
        <div class="text-center">
            <div class="text-4xl mb-4">🪙</div>
            <h3 class="text-2xl font-bold text-white mb-2">Buy Tokens</h3>
            <p class="text-gray-400 mb-2" x-show="insufficientTokensInfo">You need <span class="text-white font-semibold" x-text="insufficientTokensInfo?.required"></span> tokens. You have <span class="text-yellow-400 font-semibold" x-text="insufficientTokensInfo?.balance"></span>.</p>
            <p class="text-gray-400 mb-6">Tokens never expire. First purchase removes ads.</p>
            <div class="grid grid-cols-2 gap-3 mb-6">
                <button @click="buyPack('starter')" class="text-left bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl p-4 transition">
                    <div class="text-sm font-semibold text-white">Starter</div>
                    <div class="text-yellow-400 text-lg font-bold">50 🪙</div>
                    <div class="text-gray-400 text-xs">$5 · $0.10/token</div>
                </button>
                <button @click="buyPack('builder')" class="text-left bg-gray-800 hover:bg-gray-700 border border-purple-700 rounded-xl p-4 transition">
                    <div class="text-sm font-semibold text-white">Builder <span class="text-[10px] text-purple-400">Popular</span></div>
                    <div class="text-yellow-400 text-lg font-bold">150 🪙</div>
                    <div class="text-gray-400 text-xs">$12 · $0.08/token</div>
                </button>
                <button @click="buyPack('pro')" class="text-left bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl p-4 transition">
                    <div class="text-sm font-semibold text-white">Pro</div>
                    <div class="text-yellow-400 text-lg font-bold">500 🪙</div>
                    <div class="text-gray-400 text-xs">$29 · $0.058/token</div>
                </button>
                <button @click="buyPack('team')" class="text-left bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl p-4 transition">
                    <div class="text-sm font-semibold text-white">Team</div>
                    <div class="text-yellow-400 text-lg font-bold">2000 🪙</div>
                    <div class="text-gray-400 text-xs">$79 · $0.04/token</div>
                </button>
                <button @click="buyPack('test')" class="text-left bg-gray-800 hover:bg-gray-700 border border-green-700 rounded-xl p-4 transition">
                    <div class="text-sm font-semibold text-white">Test Pack <span class="text-[10px] text-green-400">Testing</span></div>
                    <div class="text-yellow-400 text-lg font-bold">1B 🪙</div>
                    <div class="text-gray-400 text-xs">$1 · testing only</div>
                </button>
            </div>
            <button @click="showTokenShop=false" class="text-sm text-gray-500 hover:text-gray-300">Maybe later</button>
        </div>
    </div>
</div>

<!-- Insufficient tokens modal -->
<div x-show="showInsufficientModal" x-transition class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" @click.self="showInsufficientModal=false">
    <div class="bg-gray-900 border border-gray-700 rounded-2xl p-8 max-w-md w-full mx-4 shadow-2xl text-center">
        <div class="text-4xl mb-4">🪙</div>
        <h3 class="text-xl font-bold text-white mb-2">Not Enough Tokens</h3>
        <p class="text-gray-400 mb-4">You need <span class="text-white font-semibold" x-text="insufficientTokensInfo?.required"></span> tokens but only have <span class="text-yellow-400 font-semibold" x-text="insufficientTokensInfo?.balance"></span>.</p>
        <button @click="showInsufficientModal=false; showTokenShop=true" class="w-full bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white font-semibold py-3 rounded-xl transition mb-3">Buy Tokens</button>
        <button @click="showInsufficientModal=false" class="text-sm text-gray-500 hover:text-gray-300">Cancel</button>
    </div>
</div>

<!-- Top bar -->
<div class="h-12 border-b border-gray-800 flex items-center px-4 gap-4 shrink-0 bg-gray-950/80 backdrop-blur">
    <a href="/" class="text-lg font-bold hover:opacity-80"><span class="text-purple-400">Repo</span>LM</a>
    
    <template x-if="repoData.name">
        <div class="flex items-center gap-3 text-sm text-gray-400">
            <span class="text-white font-medium" x-text="repoData.name"></span>
            <span x-text="repoData.file_count + ' files'"></span>
            <span x-text="Object.keys(repoData.languages||{}).slice(0,3).join(', ')"></span>
        </div>
    </template>

    <div class="ml-auto flex items-center gap-3">
        <template x-if="repoReady">
            <div class="flex items-center gap-2 text-xs">
                <span class="text-gray-500">Depth:</span>
                <button @click="depth='high-level'" :class="depth==='high-level'?'bg-purple-600 text-white':'bg-gray-800 text-gray-400'" class="px-2 py-1 rounded">High</button>
                <button @click="depth='low-level'" :class="depth==='low-level'?'bg-purple-600 text-white':'bg-gray-800 text-gray-400'" class="px-2 py-1 rounded">Low</button>
                <span class="text-gray-500 ml-2">Level:</span>
                <button @click="expertise='beginner'" :class="expertise==='beginner'?'bg-purple-600 text-white':'bg-gray-800 text-gray-400'" class="px-2 py-1 rounded">Beginner</button>
                <button @click="expertise='amateur'" :class="expertise==='amateur'?'bg-purple-600 text-white':'bg-gray-800 text-gray-400'" class="px-2 py-1 rounded">Amateur</button>
                <button @click="expertise='expert'" :class="expertise==='expert'?'bg-purple-600 text-white':'bg-gray-800 text-gray-400'" class="px-2 py-1 rounded">Expert</button>
            </div>
        </template>
        <!-- Save repo button -->
        <template x-if="repoReady && user && !savedDbId">
            <button @click="saveRepo()" class="text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 px-2 py-1 rounded">💾 Save</button>
        </template>
        <template x-if="savedDbId">
            <span class="text-xs text-green-400">✓ Saved</span>
        </template>
        <!-- Token balance & buy tokens -->
        <template x-if="user">
            <div class="flex items-center gap-2">
                <span class="text-xs bg-gray-800 text-yellow-400 px-2 py-1 rounded-lg">🪙 <span x-text="userTokens"></span> tokens</span>
                <button @click="showTokenShop=true" class="text-xs bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white px-3 py-1.5 rounded-lg flex items-center gap-1">🪙 Buy Tokens</button>
            </div>
        </template>
        <!-- Auth -->
        <template x-if="user">
            <div class="flex items-center gap-2 text-xs">
                <img :src="user.avatar_url" class="w-6 h-6 rounded-full">
                <span class="text-gray-300" x-text="user.username"></span>
                <a href="/auth/logout" class="text-gray-500 hover:text-gray-300">Logout</a>
            </div>
        </template>
        <template x-if="!user && authChecked">
            <button @click="showAuthModal=true" class="text-xs bg-purple-600 hover:bg-purple-500 text-white px-3 py-1.5 rounded">
                Sign in
            </button>
        </template>
    </div>
</div>

<!-- Ad banner (free users only) -->
<div x-show="showAds" class="px-4 py-1 shrink-0">
    <div class="ad-slot ad-banner flex items-center justify-center">
        <span class="ad-label">Ad</span>
        <template x-if="adsenseConfigured">
            <ins class="adsbygoogle" style="display:inline-block;width:728px;height:90px" data-ad-client="ca-pub-XXXX" data-ad-slot="BANNER_SLOT_ID"></ins>
        </template>
        <template x-if="!adsenseConfigured">
            <span class="text-gray-600 text-xs">Ad</span>
        </template>
    </div>
</div>

<!-- Landing (no repo loaded) -->
<template x-if="!repoId && !exampleLoaded">
    <div class="flex-1 flex items-center justify-center">
        <div class="text-center max-w-lg">
            <h1 class="text-5xl font-bold mb-3"><span class="text-purple-400">Repo</span>LM</h1>
            <p class="text-gray-400 mb-8">Paste a GitHub repo or upload a folder. Learn anything.</p>
            <div class="flex gap-2">
                <input x-model="urlInput" type="text" placeholder="https://github.com/user/repo"
                    @keydown.enter="loadRepo()"
                    class="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 text-white placeholder-gray-500 focus:outline-none focus:border-purple-500">
                <button @click="loadRepo()" :disabled="loadingRepo" class="bg-purple-600 hover:bg-purple-500 disabled:bg-gray-700 px-6 py-3 rounded-lg font-medium">
                    <span x-show="!loadingRepo">Go</span>
                    <span x-show="loadingRepo" class="spinner"></span>
                </button>
            </div>
            <div class="mt-3 flex items-center gap-3">
                <div class="flex-1 h-px bg-gray-800"></div>
                <span class="text-xs text-gray-600">or</span>
                <div class="flex-1 h-px bg-gray-800"></div>
            </div>
            <div class="mt-3">
                <label class="flex items-center justify-center gap-2 bg-gray-900 hover:bg-gray-800 border border-gray-700 border-dashed rounded-lg px-4 py-3 cursor-pointer transition"
                       @dragover.prevent="$el.classList.add('border-purple-500')"
                       @dragleave="$el.classList.remove('border-purple-500')"
                       @drop.prevent="$el.classList.remove('border-purple-500'); handleDrop($event)">
                    <span class="text-gray-400 text-sm">📁 Upload a folder</span>
                    <input type="file" webkitdirectory directory multiple class="hidden" @change="handleFolderUpload($event)">
                </label>
            </div>
            <p x-show="repoError" class="mt-3 text-red-400 text-sm" x-text="repoError"></p>

            <!-- Saved repos -->
            <template x-if="savedRepos.length > 0">
                <div class="mt-8 text-left">
                    <p class="text-xs text-gray-500 uppercase tracking-wider mb-3">Your Repos</p>
                    <div class="space-y-2">
                        <template x-for="r in savedRepos" :key="r.id">
                            <button @click="loadSavedRepo(r.id)" class="w-full flex items-center gap-3 bg-gray-900 hover:bg-gray-800 border border-gray-800 rounded-lg px-4 py-3 transition text-left">
                                <div class="flex-1 min-w-0">
                                    <p class="text-sm text-white font-medium truncate" x-text="r.name"></p>
                                    <p class="text-xs text-gray-500" x-text="r.file_count + ' files · ' + Object.keys(r.languages||{}).slice(0,3).join(', ')"></p>
                                </div>
                                <button @click.stop="deleteSavedRepo(r.id)" class="text-gray-600 hover:text-red-400 text-xs shrink-0">✕</button>
                            </button>
                        </template>
                    </div>
                </div>
            </template>

            <div class="mt-6 text-sm text-gray-600">
                <p>Try: expressjs/express · pallets/flask</p>
            </div>
        </div>
    </div>
</template>

<!-- Loading repo -->
<template x-if="repoId && !repoReady && !repoError">
    <div class="flex-1 flex items-center justify-center">
        <div class="text-center">
            <div class="spinner mb-4" style="width:32px;height:32px;border-width:3px"></div>
            <p class="text-white font-medium" x-text="repoMessage"></p>
        </div>
    </div>
</template>

<!-- Main 3-panel layout -->
<template x-if="repoReady || exampleLoaded">
    <div class="flex-1 flex overflow-hidden">

        <!-- LEFT: File tree -->
        <div class="w-64 border-r border-gray-800 flex flex-col shrink-0 bg-gray-950">
            <div class="px-3 py-2 border-b border-gray-800 text-xs text-gray-500 font-medium uppercase tracking-wider">Files</div>
            <div class="flex-1 overflow-y-auto file-tree p-2">
                <!-- Loading skeleton -->
                <template x-if="loadingFiles">
                    <div class="space-y-1">
                        <template x-for="i in 12" :key="i">
                            <div class="skeleton h-6 w-full" :style="'opacity:'+(1-i*0.06)+';width:'+(60+Math.random()*40)+'%'"></div>
                        </template>
                    </div>
                </template>
                <template x-for="f in files" :key="f.path">
                    <button @click="openFile(f.path)"
                        :class="currentFile===f.path ? 'bg-purple-900/30 text-purple-300' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-900'"
                        class="w-full text-left text-xs px-2 py-1.5 rounded flex items-center gap-2 transition">
                        <span :class="f.is_priority ? 'text-yellow-500' : 'text-gray-600'" x-text="f.is_priority ? '★' : '·'"></span>
                        <span class="truncate" x-text="f.path"></span>
                        <span class="ml-auto text-gray-600 text-[10px]" x-text="(f.size/1024).toFixed(1)+'k'"></span>
                    </button>
                </template>
            </div>
        </div>

        <!-- CENTER: Chat or File viewer -->
        <div class="flex-1 flex flex-col min-w-0">
            <div class="flex border-b border-gray-800 px-4 shrink-0">
                <button @click="mode='chat'" :class="mode==='chat'?'border-purple-500 text-purple-400':'border-transparent text-gray-500 hover:text-gray-300'" class="px-4 py-2 text-sm font-medium border-b-2 transition">💬 Chat</button>
                <button @click="mode='viewer'" :class="mode==='viewer'?'border-purple-500 text-purple-400':'border-transparent text-gray-500 hover:text-gray-300'" class="px-4 py-2 text-sm font-medium border-b-2 transition">📄 Viewer</button>
                <button @click="mode='immersive'" x-show="currentFile" :class="mode==='immersive'?'border-purple-500 text-purple-400':'border-transparent text-gray-500 hover:text-gray-300'" class="px-4 py-2 text-sm font-medium border-b-2 transition">🔍 Immersive</button>
            </div>

            <!-- Chat mode -->
            <div x-show="mode==='chat'" class="flex-1 flex flex-col">
                <div class="flex-1 overflow-y-auto chat-area p-4 space-y-4" x-ref="chatScroll">
                    <template x-if="messages.length===0">
                        <div class="text-center text-gray-500 mt-20">
                            <p class="text-lg mb-2">Ask anything about <span class="text-white" x-text="repoData.name || exampleData.name"></span></p>
                            <p class="text-sm">Try: "How does the routing work?" or "Explain the architecture"</p>
                        </div>
                    </template>
                    <template x-for="(msg, i) in messages" :key="i">
                        <div>
                            <!-- Inline ad every 5 messages -->
                            <div x-show="showAds && i > 0 && i % 5 === 0" class="ad-slot ad-inline flex items-center justify-center my-2">
                                <span class="ad-label">Ad</span>
                                <template x-if="adsenseConfigured"><ins class="adsbygoogle" style="display:inline-block;width:100%;height:60px" data-ad-client="ca-pub-XXXX" data-ad-slot="INLINE_SLOT_ID"></ins></template>
                                <template x-if="!adsenseConfigured"><span class="text-gray-600 text-xs">Ad</span></template>
                            </div>
                            <div :class="msg.role==='user' ? 'flex justify-end' : ''" class="msg-enter">
                                <div :class="msg.role==='user' ? 'bg-purple-900/40 border-purple-800' : 'bg-gray-900 border-gray-800'" class="border rounded-xl px-4 py-3 max-w-[85%]">
                                    <div x-show="msg.role==='user'" class="text-sm text-white" x-text="msg.text"></div>
                                    <div x-show="msg.role==='assistant'" class="prose prose-invert prose-sm max-w-none" x-html="renderMd(msg.text)"></div>
                                    <div x-show="msg.role==='loading'" class="flex items-center gap-2 text-gray-400 text-sm">
                                        <span class="spinner"></span> Thinking...
                                    </div>
                                </div>
                            </div>
                        </div>
                    </template>
                </div>
                <div class="border-t border-gray-800 p-4">
                    <div class="flex gap-2">
                        <input x-model="chatInput" type="text" placeholder="Ask about this codebase..."
                            @keydown.enter="sendChat()"
                            :disabled="chatLoading || exampleLoaded"
                            class="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 disabled:opacity-50">
                        <button @click="sendChat()" :disabled="chatLoading || exampleLoaded" class="bg-purple-600 hover:bg-purple-500 disabled:bg-gray-700 px-4 py-2.5 rounded-lg text-sm">Send (2 🪙)</button>
                    </div>
                    <p x-show="exampleLoaded" class="text-xs text-gray-500 mt-2">Chat disabled in example mode. <a href="/app" class="text-purple-400 hover:underline">Load your own repo</a> to chat.</p>
                </div>
            </div>

            <!-- Viewer mode -->
            <div x-show="mode==='viewer'" class="flex-1 overflow-y-auto viewer p-4">
                <template x-if="!currentFile && !viewingGenerated">
                    <div class="text-center text-gray-500 mt-20">Select a file from the sidebar or generate content</div>
                </template>
                <template x-if="currentFile || viewingGenerated">
                    <div>
                        <div class="flex items-center justify-between mb-3">
                            <h3 class="text-sm font-mono text-gray-300" x-text="viewingGenerated ? viewingGenerated.label : currentFile"></h3>
                            <div class="flex gap-2">
                                <!-- Download button -->
                                <button @click="downloadContent()" class="text-xs text-gray-500 hover:text-purple-400 bg-gray-900 border border-gray-800 px-3 py-1.5 rounded-lg transition flex items-center gap-1">
                                    ⬇️ Download
                                </button>
                                <!-- Listen button for podcast -->
                                <button x-show="viewingGenerated && viewingGenerated.kind==='podcast'"
                                    @click="generateAudio()"
                                    :disabled="audioGenerating"
                                    class="text-xs text-gray-500 hover:text-purple-400 bg-gray-900 border border-gray-800 px-3 py-1.5 rounded-lg transition flex items-center gap-1 disabled:opacity-50">
                                    <span x-show="!audioGenerating">🎧 Listen (30 🪙)</span>
                                    <span x-show="audioGenerating" class="flex items-center gap-1"><span class="spinner" style="width:12px;height:12px;border-width:1.5px"></span> Generating audio...</span>
                                </button>
                            </div>
                        </div>
                        <!-- Audio player -->
                        <div x-show="audioUrl" class="mb-4 bg-gray-900 border border-gray-800 rounded-xl p-3">
                            <audio :src="audioUrl" controls class="w-full" style="height:40px"></audio>
                        </div>
                        <!-- Content -->
                        <div x-show="viewingGenerated" class="prose prose-invert prose-sm max-w-none" x-html="renderMd(viewingContent)"></div>
                        <pre x-show="!viewingGenerated" class="bg-gray-900 rounded-lg p-4 text-sm overflow-x-auto"><code x-html="highlightCode(fileContent, currentFile)"></code></pre>
                    </div>
                </template>
            </div>

            <!-- Immersive mode -->
            <div x-show="mode==='immersive'" class="flex-1 flex overflow-hidden">
                <div class="flex-1 overflow-y-auto p-4" @mouseup="handleSelection()">
                    <template x-if="currentFile && fileContent">
                        <div>
                            <div class="flex items-center gap-3 mb-3">
                                <h3 class="text-sm font-mono text-gray-300" x-text="currentFile"></h3>
                                <span class="text-xs text-purple-400 bg-purple-900/30 px-2 py-0.5 rounded">Highlight text to ask about it</span>
                            </div>
                            <pre class="bg-gray-900 rounded-lg p-4 text-sm overflow-x-auto leading-relaxed"><code x-html="highlightCode(fileContent, currentFile)"></code></pre>
                        </div>
                    </template>
                </div>
                <div class="w-80 border-l border-gray-800 flex flex-col shrink-0 h-full min-h-0">
                    <div class="px-3 py-2 border-b border-gray-800 text-xs text-gray-500 font-medium uppercase tracking-wider" x-text="selectedText ? 'Ask about selection' : 'Ask about this file'"></div>
                    <div x-show="selectedText" class="px-3 py-2 border-b border-gray-800 bg-purple-900/20 flex items-start gap-2">
                        <div class="flex-1 min-w-0">
                            <p class="text-xs text-purple-300 mb-1">Selected:</p>
                            <p class="text-xs text-gray-300 font-mono line-clamp-3" x-text="selectedText"></p>
                        </div>
                        <button @click="selectedText=''" class="text-gray-500 hover:text-white text-sm shrink-0 mt-0.5">✕</button>
                    </div>
                    <div class="flex-1 overflow-y-auto min-h-0 p-3 space-y-3">
                        <template x-for="(msg, i) in immersiveMessages" :key="i">
                            <div :class="msg.role==='user' ? 'bg-purple-900/30 border-purple-800' : 'bg-gray-900 border-gray-800'" class="border rounded-lg px-3 py-2 text-sm">
                                <div x-show="msg.role==='user'" class="text-white text-xs" x-text="msg.text"></div>
                                <div x-show="msg.role==='assistant'" class="prose prose-invert prose-xs max-w-none" x-html="renderMd(msg.text)"></div>
                                <div x-show="msg.role==='loading'" class="flex items-center gap-2 text-gray-400 text-xs"><span class="spinner"></span></div>
                            </div>
                        </template>
                    </div>
                    <div class="border-t border-gray-800 p-2">
                        <div class="flex gap-1">
                            <input x-model="immersiveInput" type="text" :placeholder="selectedText ? 'Ask about selection...' : 'Ask about this file...'"
                                @keydown.enter="sendImmersive()"
                                class="flex-1 bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-white placeholder-gray-500 focus:outline-none focus:border-purple-500">
                            <button @click="sendImmersive()" class="bg-purple-600 hover:bg-purple-500 px-2 py-1.5 rounded text-xs">Ask</button>
                        </div>
                        <div class="flex gap-1 mt-1">
                            <button @click="immersiveInput='Explain this';sendImmersive()" class="text-[10px] text-gray-500 hover:text-purple-400 bg-gray-900 px-2 py-1 rounded">Explain</button>
                            <button @click="immersiveInput='Why is it done this way?';sendImmersive()" class="text-[10px] text-gray-500 hover:text-purple-400 bg-gray-900 px-2 py-1 rounded">Why?</button>
                            <button @click="immersiveInput='How could this be improved?';sendImmersive()" class="text-[10px] text-gray-500 hover:text-purple-400 bg-gray-900 px-2 py-1 rounded">Improve?</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- RIGHT: Generate panel -->
        <div class="w-72 border-l border-gray-800 flex flex-col shrink-0 bg-gray-950">
            <div class="px-3 py-2 border-b border-gray-800 text-xs text-gray-500 font-medium uppercase tracking-wider">Generate</div>
            <div class="p-3 space-y-2">
                <button @click="doGenerate('overview')" :disabled="generating || exampleLoaded"
                    class="w-full text-left bg-gray-900 hover:bg-gray-800 border border-gray-800 rounded-lg p-3 transition disabled:opacity-50">
                    <div class="flex items-center gap-2 text-sm font-medium">📖 Overview <span class="text-yellow-400 text-xs">(10 🪙)</span></div>
                    <p class="text-xs text-gray-500 mt-1">Architecture, concepts, how it works</p>
                </button>
                <button @click="doGenerate('podcast')" :disabled="generating || exampleLoaded"
                    class="w-full text-left bg-gray-900 hover:bg-gray-800 border border-gray-800 rounded-lg p-3 transition disabled:opacity-50">
                    <div class="flex items-center gap-2 text-sm font-medium">🎙️ Podcast Script <span class="text-yellow-400 text-xs">(20 🪙)</span></div>
                    <p class="text-xs text-gray-500 mt-1">Two-host conversation about the codebase</p>
                </button>
                <button @click="doGenerate('slides')" :disabled="generating || exampleLoaded"
                    class="w-full text-left bg-gray-900 hover:bg-gray-800 border border-gray-800 rounded-lg p-3 transition disabled:opacity-50">
                    <div class="flex items-center gap-2 text-sm font-medium">📊 Slide Deck <span class="text-yellow-400 text-xs">(15 🪙)</span></div>
                    <p class="text-xs text-gray-500 mt-1">Presentation-ready breakdown</p>
                </button>
            </div>
            
            <div x-show="generating" class="px-3 py-2">
                <div class="flex items-center gap-2 text-sm text-purple-400"><span class="spinner"></span><span x-text="genMessage"></span></div>
            </div>

            <div class="flex-1 overflow-y-auto px-3 py-2 space-y-2">
                <template x-for="(item, i) in generated" :key="i">
                    <button @click="viewGenerated(item)" class="w-full text-left bg-gray-900 hover:bg-gray-800 border border-gray-800 rounded-lg p-2 transition">
                        <div class="text-xs font-medium text-purple-300" x-text="item.kind.charAt(0).toUpperCase()+item.kind.slice(1)"></div>
                        <div class="text-[10px] text-gray-500 mt-0.5" x-text="item.depth + ' · ' + item.expertise"></div>
                    </button>
                </template>
            </div>
            <!-- Sidebar ad (free users) -->
            <div x-show="showAds" class="px-3 py-2 shrink-0">
                <div class="ad-slot ad-sidebar flex items-center justify-center">
                    <span class="ad-label">Ad</span>
                    <template x-if="adsenseConfigured">
                        <ins class="adsbygoogle" style="display:block;width:100%;min-height:250px" data-ad-client="ca-pub-XXXX" data-ad-slot="SIDEBAR_SLOT_ID" data-ad-format="auto"></ins>
                    </template>
                    <template x-if="!adsenseConfigured">
                        <span class="text-gray-600 text-xs">Ad</span>
                    </template>
                </div>
            </div>
        </div>

    </div>
</template>

</div>

<script>
function repolm() {
    return {
        urlInput: '',
        repoId: null,
        repoData: {},
        repoReady: false,
        repoError: null,
        repoMessage: '',
        loadingRepo: false,
        loadingFiles: false,
        files: [],
        currentFile: null,
        fileContent: null,
        mode: 'chat',
        depth: 'high-level',
        expertise: 'amateur',
        messages: [],
        chatInput: '',
        chatLoading: false,
        selectedText: '',
        immersiveMessages: [],
        immersiveInput: '',
        generating: false,
        genMessage: '',
        generated: [],
        viewingGenerated: null,
        viewingContent: '',
        audioGenerating: false,
        audioUrl: null,
        exampleLoaded: false,
        exampleData: {},
        user: null,
        authChecked: false,
        showAuthModal: false,
        authMode: 'login',
        authEmail: '',
        authPassword: '',
        authUsername: '',
        authError: '',
        authLoading: false,
        savedRepos: [],
        savedDbId: null,
        showTokenShop: false,
        showInsufficientModal: false,
        insufficientTokensInfo: null,
        checkoutSuccess: false,
        userTokens: 0,
        userHasPurchased: false,

        get showAds() { return !this.user || !this.userHasPurchased; },
        get adsenseConfigured() { return window.ADSENSE_CLIENT_ID && window.ADSENSE_CLIENT_ID !== '' && !window.ADSENSE_CLIENT_ID.includes('__'); },
        get isPro() { return false; },

        init() {
            this.checkAuth();
            const params = new URLSearchParams(window.location.search);
            const exSlug = params.get('example');
            if (exSlug) this.loadExample(exSlug);
            if (params.get('checkout') === 'success') {
                this.checkoutSuccess = true;
                window.history.replaceState({}, '', '/app');
                setTimeout(() => { this.checkoutSuccess = false; this.refreshTokens(); }, 2000);
            }
        },

        async submitAuth() {
            this.authError = '';
            this.authLoading = true;
            const endpoint = this.authMode === 'login' ? '/auth/login' : '/auth/signup';
            const body = {email: this.authEmail, password: this.authPassword};
            if (this.authMode === 'signup') body.username = this.authUsername;
            try {
                const res = await fetch(endpoint, {
                    method: 'POST', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                if (!res.ok) { this.authError = data.error || 'Something went wrong'; this.authLoading = false; return; }
                this.showAuthModal = false;
                this.authEmail = ''; this.authPassword = ''; this.authUsername = '';
                await this.checkAuth();
            } catch(e) { this.authError = 'Connection failed'; }
            this.authLoading = false;
        },

        async checkAuth() {
            try {
                const res = await fetch('/auth/me');
                const data = await res.json();
                this.user = data.user;
                if (this.user) {
                    this.userTokens = data.user.tokens || 0;
                    this.userHasPurchased = data.user.has_purchased || false;
                    this.loadSavedRepos();
                }
            } catch(e) {}
            this.authChecked = true;
        },

        async refreshTokens() {
            try {
                const res = await fetch('/auth/me');
                const data = await res.json();
                if (data.user) {
                    this.userTokens = data.user.tokens || 0;
                    this.userHasPurchased = data.user.has_purchased || false;
                }
            } catch(e) {}
        },

        async buyPack(pack) {
            try {
                const res = await fetch('/api/checkout', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({pack})});
                const data = await res.json();
                if (data.url) window.location.href = data.url;
                else if (data.error) alert(data.error);
            } catch(e) { alert('Failed to start checkout'); }
        },

        handleInsufficientTokens(required, balance) {
            this.insufficientTokensInfo = {required, balance};
            this.showInsufficientModal = true;
        },

        async loadSavedRepos() {
            try {
                const res = await fetch('/api/my/repos');
                if (res.ok) this.savedRepos = await res.json();
            } catch(e) {}
        },

        async saveRepo() {
            if (!this.repoId || !this.user) return;
            try {
                const res = await fetch(`/api/my/repos/${this.repoId}/save`, {method:'POST'});
                const data = await res.json();
                if (data.db_id) {
                    this.savedDbId = data.db_id;
                    this.loadSavedRepos();
                }
            } catch(e) {}
        },

        async loadSavedRepo(dbId) {
            this.loadingRepo = true;
            try {
                const res = await fetch(`/api/my/repos/${dbId}`);
                const data = await res.json();
                if (data.repo_id) {
                    this.repoId = data.repo_id;
                    this.repoData = data.data;
                    this.savedDbId = dbId;
                    this.repoReady = true;
                    this.loadingRepo = false;
                    // Load file index (no content — need to load individually or re-clone)
                    this.files = data.file_index || [];
                    // Load saved chat history
                    const chatRes = await fetch(`/api/my/repos/${dbId}/chats`);
                    if (chatRes.ok) {
                        const chats = await chatRes.json();
                        this.messages = chats.map(c => ({role: c.role, text: c.message}));
                    }
                    // Load saved generated content
                    const genRes = await fetch(`/api/my/repos/${dbId}/generated`);
                    if (genRes.ok) {
                        const gens = await genRes.json();
                        this.generated = gens.map(g => ({kind:g.kind, depth:g.depth, expertise:g.expertise, content:g.content}));
                    }
                }
            } catch(e) { this.repoError = 'Failed to load saved repo'; }
            this.loadingRepo = false;
        },

        async deleteSavedRepo(dbId) {
            if (!confirm('Delete this saved repo?')) return;
            await fetch(`/api/my/repos/${dbId}`, {method:'DELETE'});
            this.savedRepos = this.savedRepos.filter(r => r.id !== dbId);
        },

        async loadExample(slug) {
            try {
                const res = await fetch(`/api/examples/${slug}`);
                if (!res.ok) return;
                const data = await res.json();
                this.exampleLoaded = true;
                this.exampleData = data;
                this.repoData = { name: data.name, file_count: 0, languages: {} };
                this.files = [];
                // Load pre-generated content
                if (data.overview) this.generated.push({kind:'overview', depth:data.depth||'high-level', expertise:data.expertise||'amateur', content:data.overview});
                if (data.podcast) this.generated.push({kind:'podcast', depth:data.depth||'high-level', expertise:data.expertise||'amateur', content:data.podcast});
                if (this.generated.length) this.viewGenerated(this.generated[0]);
            } catch(e) { console.error(e); }
        },

        async handleFolderUpload(event) {
            const files = event.target.files;
            if (!files || files.length === 0) return;
            await this.uploadFiles(files);
        },

        async handleDrop(event) {
            // Handle drag & drop of folders
            const items = event.dataTransfer.items;
            if (!items) return;
            const files = event.dataTransfer.files;
            if (files.length > 0) await this.uploadFiles(files);
        },

        async uploadFiles(files) {
            this.loadingRepo = true;
            this.repoError = null;
            const formData = new FormData();
            for (const file of files) {
                // webkitRelativePath gives us folder/path/file.ext
                const path = file.webkitRelativePath || file.name;
                formData.append(path, file, path);
            }
            try {
                const res = await fetch('/api/upload', {method: 'POST', body: formData});
                const data = await res.json();
                if (data.error) { this.repoError = data.error; this.loadingRepo = false; return; }
                this.repoId = data.repo_id;
                this.pollRepo();
            } catch(e) {
                this.repoError = 'Upload failed';
                this.loadingRepo = false;
            }
        },

        async loadRepo() {
            let url = this.urlInput.trim();
            if (!url) return;
            if (!url.startsWith('http')) url = 'https://github.com/' + url;
            this.loadingRepo = true;
            this.repoError = null;
            try {
                const res = await fetch('/api/repo', {
                    method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({url})
                });
                const data = await res.json();
                if (res.status === 402) { this.handleInsufficientTokens(data.required, data.balance); this.loadingRepo = false; return; }
                if (data.error) { this.repoError = data.error; this.loadingRepo = false; return; }
                this.repoId = data.repo_id;
                this.pollRepo();
            } catch(e) {
                this.repoError = 'Failed to connect to server';
                this.loadingRepo = false;
            }
        },

        async pollRepo() {
            const iv = setInterval(async () => {
                try {
                    const res = await fetch(`/api/repo/${this.repoId}`);
                    const data = await res.json();
                    this.repoMessage = data.message;
                    this.repoData = data.data || {};
                    if (data.status === 'ready') {
                        clearInterval(iv);
                        this.repoReady = true;
                        this.loadingRepo = false;
                        this.loadFiles();
                    } else if (data.status === 'error') {
                        clearInterval(iv);
                        this.repoError = data.message;
                        this.repoId = null;
                        this.loadingRepo = false;
                    }
                } catch(e) {
                    clearInterval(iv);
                    this.repoError = 'Connection lost';
                    this.loadingRepo = false;
                }
            }, 1500);
        },

        async loadFiles() {
            this.loadingFiles = true;
            try {
                const res = await fetch(`/api/repo/${this.repoId}/files`);
                this.files = await res.json();
            } catch(e) {}
            this.loadingFiles = false;
        },

        async openFile(path) {
            this.currentFile = path;
            this.viewingGenerated = null;
            this.audioUrl = null;
            try {
                const res = await fetch(`/api/repo/${this.repoId}/file?path=${encodeURIComponent(path)}`);
                const data = await res.json();
                this.fileContent = data.content;
            } catch(e) { this.fileContent = 'Failed to load file'; }
            if (this.mode === 'chat') this.mode = 'viewer';
        },

        async sendChat() {
            const text = this.chatInput.trim();
            if (!text || this.chatLoading) return;
            this.chatInput = '';
            this.messages.push({role:'user', text});
            this.messages.push({role:'loading', text:''});
            this.chatLoading = true;
            this.scrollChat();
            try {
                const res = await fetch(`/api/repo/${this.repoId}/chat`, {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({message:text, depth:this.depth, expertise:this.expertise})
                });
                const data = await res.json();
                if (res.status === 402) { this.messages.pop(); this.handleInsufficientTokens(data.required, data.balance); this.chatLoading = false; return; }
                this.messages.pop();
                if (data.balance !== undefined && data.balance !== null) this.userTokens = data.balance;
                const reply = data.response || data.error || 'Something went wrong';
                this.messages.push({role:'assistant', text: reply});
                // Auto-save chat if logged in
                if (this.user && this.savedDbId) {
                    fetch(`/api/my/repos/${this.savedDbId}/chat`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({role:'user', message:text})});
                    fetch(`/api/my/repos/${this.savedDbId}/chat`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({role:'assistant', message:reply})});
                }
            } catch(e) {
                this.messages.pop();
                this.messages.push({role:'assistant', text: 'Error: Failed to get response. Please try again.'});
            }
            this.chatLoading = false;
            this.scrollChat();
            this.$nextTick(() => document.querySelectorAll('.chat-area pre code').forEach(el => hljs.highlightElement(el)));
        },

        handleSelection() {
            const sel = window.getSelection().toString().trim();
            if (sel.length > 5) this.selectedText = sel;
        },

        async sendImmersive() {
            const text = this.immersiveInput.trim() || 'Explain this';
            this.immersiveInput = '';
            this.immersiveMessages.push({role:'user', text: (this.selectedText ? `[${this.selectedText.slice(0,80)}...]\n` : '') + text});
            this.immersiveMessages.push({role:'loading', text:''});
            try {
                const res = await fetch(`/api/repo/${this.repoId}/chat`, {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({message:text, depth:this.depth, expertise:this.expertise, selection:this.selectedText, file_path:this.currentFile})
                });
                const data = await res.json();
                if (res.status === 402) { this.immersiveMessages.pop(); this.handleInsufficientTokens(data.required, data.balance); return; }
                if (data.balance !== undefined && data.balance !== null) this.userTokens = data.balance;
                this.immersiveMessages.pop();
                this.immersiveMessages.push({role:'assistant', text: data.response || data.error});
            } catch(e) {
                this.immersiveMessages.pop();
                this.immersiveMessages.push({role:'assistant', text: 'Error: request failed'});
            }
            this.$nextTick(() => document.querySelectorAll('.prose pre code').forEach(el => hljs.highlightElement(el)));
        },

        async doGenerate(kind) {
            this.generating = true;
            this.genMessage = `Generating ${kind}...`;
            try {
                const res = await fetch(`/api/repo/${this.repoId}/generate`, {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({kind, depth:this.depth, expertise:this.expertise})
                });
                const data = await res.json();
                if (res.status === 402) { this.generating = false; this.handleInsufficientTokens(data.required, data.balance); return; }
                const job_id = data.job_id;
                const iv = setInterval(async () => {
                    try {
                        const r = await fetch(`/api/job/${job_id}`);
                        const d = await r.json();
                        this.genMessage = d.message;
                        if (d.status === 'done') {
                            clearInterval(iv);
                            this.generating = false;
                            const item = {kind, depth:this.depth, expertise:this.expertise, content:d.result};
                            this.generated.push(item);
                            this.viewGenerated(item);
                            // Auto-save if logged in
                            if (this.user && this.savedDbId) {
                                fetch(`/api/my/repos/${this.savedDbId}/generated`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(item)});
                            }
                        } else if (d.status === 'error') {
                            clearInterval(iv);
                            this.generating = false;
                            this.messages.push({role:'assistant', text:'Error generating: ' + d.message});
                        }
                    } catch(e) { clearInterval(iv); this.generating = false; }
                }, 2000);
            } catch(e) {
                this.generating = false;
                this.messages.push({role:'assistant', text:'Error: failed to start generation'});
            }
        },

        viewGenerated(item) {
            this.mode = 'viewer';
            this.viewingGenerated = {kind: item.kind, label: '📄 ' + item.kind.charAt(0).toUpperCase() + item.kind.slice(1)};
            this.viewingContent = item.content;
            this.currentFile = null;
            this.audioUrl = null;
            this.$nextTick(() => document.querySelectorAll('.viewer .prose pre code').forEach(el => hljs.highlightElement(el)));
        },

        downloadContent() {
            let content, filename;
            if (this.viewingGenerated) {
                content = this.viewingContent;
                filename = (this.repoData.name || 'repo') + '_' + this.viewingGenerated.kind + '.md';
            } else {
                content = this.fileContent;
                filename = (this.currentFile || 'file').split('/').pop();
            }
            const blob = new Blob([content], {type:'text/markdown'});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = filename; a.click();
            URL.revokeObjectURL(url);
        },

        async generateAudio() {
            if (!this.viewingContent || this.audioGenerating) return;
            this.audioGenerating = true;
            this.audioUrl = null;
            try {
                const res = await fetch('/api/podcast-audio', {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({script: this.viewingContent})
                });
                const {audio_id} = await res.json();
                const iv = setInterval(async () => {
                    try {
                        const r = await fetch(`/api/podcast-audio/${audio_id}`);
                        const d = await r.json();
                        if (d.status === 'done') {
                            clearInterval(iv);
                            this.audioUrl = d.url;
                            this.audioGenerating = false;
                        } else if (d.status === 'error') {
                            clearInterval(iv);
                            this.audioGenerating = false;
                            alert('Audio generation failed: ' + (d.message || 'unknown error'));
                        }
                    } catch(e) { clearInterval(iv); this.audioGenerating = false; }
                }, 3000);
            } catch(e) {
                this.audioGenerating = false;
                alert('Failed to start audio generation');
            }
        },

        scrollChat() {
            this.$nextTick(() => {
                const el = this.$refs.chatScroll;
                if (el) el.scrollTop = el.scrollHeight;
            });
        },

        highlightCode(code, filename) {
            if (!code) return '';
            const ext = (filename||'').split('.').pop();
            const langMap = {py:'python',js:'javascript',ts:'typescript',rb:'ruby',go:'go',rs:'rust',java:'java',sh:'bash',yml:'yaml',yaml:'yaml',json:'json',md:'markdown',html:'html',css:'css'};
            const lang = langMap[ext] || ext || 'plaintext';
            try { return hljs.highlight(code, {language: lang}).value; }
            catch(e) { return code.replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
        },

        renderMd(text) {
            if (!text) return '';
            return marked.parse(text);
        }
    }
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
