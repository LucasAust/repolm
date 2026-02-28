"""
RepoLM — Generate & chat streaming endpoints.
Uses thread pools and SSE semaphore for concurrency control.
"""

import json
import uuid
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from config import (
    TOKEN_COSTS, OVERVIEW_SYSTEM, PODCAST_SYSTEM, SLIDES_SYSTEM,
    CHAT_SYSTEM, SELECTION_SYSTEM, get_system_prompt,
)
from auth import get_current_user
import db as database
import db_async
import state
import cache as content_cache
from services.llm import call_llm, call_llm_stream, call_llm_stream_messages, async_call_llm_stream, async_call_llm_stream_messages
from routes._helpers import check_rate_limit, sse_format
from concurrency import generate_queue, sse_semaphore, acquire_sse

router = APIRouter()
logger = logging.getLogger("repolm")


def run_generate(job_id, repo_id, kind, depth, expertise):
    """Background worker: generate content. Runs in thread pool (sync is fine)."""
    repo = state.get_repo_with_fallback(repo_id)
    if not repo or repo["status"] != "ready":
        database.update_job(job_id, status="error", message="Repo not ready")
        return
    try:
        text = repo["text"]
        if len(text) > 200_000:
            text = text[:200_000] + "\n\n[... truncated ...]"
        templates = {"overview": OVERVIEW_SYSTEM, "podcast": PODCAST_SYSTEM, "slides": SLIDES_SYSTEM}
        if kind not in templates:
            database.update_job(job_id, status="error", message="Unknown kind: {}".format(kind))
            return
        system = get_system_prompt(templates[kind], depth, expertise)
        prompts = {
            "overview": "Analyze this repository and provide a comprehensive overview:\n\n{}".format(text),
            "podcast": "Write a podcast script about this repository:\n\n{}".format(text),
            "slides": "Create a slide deck about this repository:\n\n{}".format(text),
        }
        database.update_job(job_id, status="generating", message="Generating {}...".format(kind))
        result = call_llm(system, prompts[kind])
        database.update_job(job_id, status="done", message="Done", result=result)
    except Exception as e:
        logger.exception("Generate failed for job %s", job_id)
        database.update_job(job_id, status="error", message=str(e))


@router.post("/api/repo/{repo_id}/generate")
async def generate(repo_id: str, request: Request):
    body = await request.json()
    kind = body.get("kind", "overview")
    depth = body.get("depth", "high-level")
    expertise = body.get("expertise", "amateur")
    cost = TOKEN_COSTS.get(kind, 10)
    user = await get_current_user(request)
    if user:
        balance = await db_async.get_token_balance(user["id"])
        if balance < cost:
            return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
        await db_async.spend_tokens(user["id"], cost, f"Generate {kind}")
    job_id = str(uuid.uuid4())[:8]
    await db_async.create_job(job_id, kind="generate", repo_id=repo_id)

    status, queue_pos = generate_queue.submit(job_id, run_generate, job_id, repo_id, kind, depth, expertise)
    if status == "rejected":
        return JSONResponse({"error": "Server busy, try again in a moment"}, 503)

    result = {"job_id": job_id}
    if status == "queued":
        result["queued"] = True
        result["queue_position"] = queue_pos
    return result


@router.get("/api/job/{job_id}")
async def get_job(job_id: str):
    job = await db_async.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Not found"}, 404)
    result = {"status": job["status"], "message": job["message"], "result": job["result"]}
    for q in (generate_queue,):
        pos = q.get_position(job_id)
        if pos is not None:
            result["queue_position"] = pos
            break
    return result


@router.post("/api/repo/{repo_id}/generate-stream")
async def generate_stream(repo_id: str, request: Request):
    """SSE streaming endpoint for generation (overview, podcast, slides)."""
    body = await request.json()
    kind = body.get("kind", "overview")
    depth = body.get("depth", "high-level")
    expertise = body.get("expertise", "amateur")

    cost = TOKEN_COSTS.get(kind, 10)
    user = await get_current_user(request)
    is_free_anon = False
    if user:
        balance = await db_async.get_token_balance(user["id"])
        if balance < cost:
            return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
        await db_async.spend_tokens(user["id"], cost, f"Generate {kind}")
    else:
        if kind == "overview":
            ip = request.client.host if request.client else "unknown"
            used = await db_async.check_anonymous_usage(ip)
            if used >= 1:
                return JSONResponse({"error": "Please sign up to continue. Your first overview was free!", "signup_required": True}, 401)
            is_free_anon = True
            await db_async.increment_anonymous_usage(ip)
        else:
            return JSONResponse({"error": "Please sign up to generate content"}, 401)

    repo = await db_async.get_repo_with_fallback(repo_id)
    if not repo or repo["status"] != "ready":
        return JSONResponse({"error": "Repo not ready"}, 400)

    ip = request.client.host if request.client else "unknown"
    sse_ctx = acquire_sse(ip)
    if not sse_ctx:
        return JSONResponse({"error": "Too many concurrent streams. Please close other tabs."}, 429)

    text = repo["text"]
    if len(text) > 200_000:
        text = text[:200_000] + "\n\n[... truncated ...]"

    templates = {"overview": OVERVIEW_SYSTEM, "podcast": PODCAST_SYSTEM, "slides": SLIDES_SYSTEM}
    if kind not in templates:
        sse_ctx.release()
        return JSONResponse({"error": f"Unknown kind: {kind}"}, 400)

    system = get_system_prompt(templates[kind], depth, expertise)
    prompts = {
        "overview": f"Analyze this repository and provide a comprehensive overview:\n\n{text}",
        "podcast": f"Write a podcast script about this repository:\n\n{text}",
        "slides": f"Create a slide deck about this repository:\n\n{text}",
    }

    repo_url = repo.get("data", {}).get("url", "")
    cached = content_cache.get_cached(repo_url, kind, depth, expertise) if repo_url else None

    async def event_stream():
        try:
            async with sse_semaphore:
                if cached:
                    yield sse_format("true", "cached")
                    chunk_size = 80
                    for i in range(0, len(cached), chunk_size):
                        yield sse_format(cached[i:i+chunk_size], "chunk")
                    yield sse_format("", "done")
                else:
                    full_content = ""
                    async for chunk in async_call_llm_stream(system, prompts[kind]):
                        full_content += chunk
                        yield sse_format(chunk, "chunk")
                    if repo_url and full_content:
                        content_cache.set_cached(repo_url, kind, depth, expertise, full_content)
                    if kind == "overview" and full_content and repo_url:
                        try:
                            import re
                            match = re.match(r'https?://github\.com/([^/]+)/([^/]+)', repo_url)
                            if match:
                                owner, rname = match.group(1), match.group(2).replace('.git', '')
                                repo_data = repo.get("data", {})
                                await db_async.save_public_overview(
                                    owner=owner, repo_name=rname, repo_url=repo_url,
                                    overview=full_content,
                                    description=full_content[:200].replace('\n', ' '),
                                    languages=str(repo_data.get("languages", "")),
                                    file_count=repo_data.get("file_count", 0),
                                    depth=depth, expertise=expertise,
                                )
                        except Exception:
                            pass
                    if user and full_content:
                        try:
                            badge_map = {"overview": "first_overview", "podcast": "podcast_pioneer", "slides": "slide_master"}
                            badge = badge_map.get(kind)
                            if badge:
                                new = await db_async.grant_achievement(user["id"], badge)
                                if new:
                                    defn = database.ACHIEVEMENT_DEFS.get(badge, {})
                                    yield sse_format(json.dumps({"badge": badge, "name": defn.get("name", badge), "emoji": defn.get("emoji", "🏆")}), "achievement")
                        except Exception:
                            pass
                    yield sse_format("", "done")
        except Exception as e:
            yield sse_format(str(e), "error")
        finally:
            sse_ctx.release()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/repo/{repo_id}/chat-stream")
async def chat_stream(repo_id: str, request: Request):
    """SSE streaming endpoint for chat."""
    if await check_rate_limit(request, "chat"):
        return JSONResponse({"error": "Rate limit exceeded (20 chats/hour)."}, 429)

    body = await request.json()
    message = body.get("message", "")
    depth = body.get("depth", "high-level")
    expertise = body.get("expertise", "amateur")
    selection = body.get("selection")
    file_context = body.get("file_path")
    repo = await db_async.get_repo_with_fallback(repo_id)
    if not repo or repo["status"] != "ready":
        return JSONResponse({"error": "Repo not ready"}, 400)

    ip = request.client.host if request.client else "unknown"
    sse_ctx = acquire_sse(ip)
    if not sse_ctx:
        return JSONResponse({"error": "Too many concurrent streams. Please close other tabs."}, 429)

    user = await get_current_user(request)
    is_immersive = bool(selection and file_context)
    cost = TOKEN_COSTS["immersive"] if is_immersive else TOKEN_COSTS["chat"]
    if user:
        balance = await db_async.get_token_balance(user["id"])
        if balance < cost:
            sse_ctx.release()
            return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)

    history = body.get("history", [])

    if selection and file_context:
        system = get_system_prompt(SELECTION_SYSTEM, depth, expertise)
        file_content = ""
        for f in repo.get("files", []):
            if f["path"] == file_context:
                file_content = f["content"]
                break
        prompt = f"File: {file_context}\n```\n{file_content}\n```\n\nHighlighted selection:\n```\n{selection}\n```\n\nUser question: {message}"
        messages_list = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
    else:
        system = get_system_prompt(CHAT_SYSTEM, depth, expertise)
        text = repo["text"]
        if len(text) > 150_000:
            text = text[:150_000] + "\n\n[... truncated ...]"
        messages_list = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Repository context (for reference, don't respond to this):\n{text}"},
            {"role": "assistant", "content": "I've loaded the repository context. Ask me anything about this codebase."},
        ]
        for h in history[-12:]:
            role = h.get("role", "user")
            content = h.get("content", "")
            if role in ("user", "assistant") and content:
                messages_list.append({"role": role, "content": content})
        messages_list.append({"role": "user", "content": message})

    async def event_stream():
        try:
            async with sse_semaphore:
                async for chunk in async_call_llm_stream_messages(messages_list):
                    yield sse_format(chunk, "chunk")
                if user:
                    await db_async.spend_tokens(user["id"], cost, "Immersive question" if is_immersive else "Chat message")
                new_balance = await db_async.get_token_balance(user["id"]) if user else None
                yield sse_format(json.dumps({"cost": cost, "balance": new_balance}), "meta")
                yield sse_format("", "done")
        except Exception as e:
            yield sse_format(str(e), "error")
        finally:
            sse_ctx.release()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/repo/{repo_id}/chat")
async def chat(repo_id: str, request: Request):
    """Non-streaming chat endpoint (legacy)."""
    if await check_rate_limit(request, "chat"):
        return JSONResponse({"error": "Rate limit exceeded (20 chats/hour)."}, 429)
    body = await request.json()
    message = body.get("message", "")
    depth = body.get("depth", "high-level")
    expertise = body.get("expertise", "amateur")
    selection = body.get("selection")
    file_context = body.get("file_path")
    repo = await db_async.get_repo_with_fallback(repo_id)
    if not repo or repo["status"] != "ready":
        return JSONResponse({"error": "Repo not ready"}, 400)
    user = await get_current_user(request)
    is_immersive = bool(selection and file_context)
    cost = TOKEN_COSTS["immersive"] if is_immersive else TOKEN_COSTS["chat"]
    if user:
        balance = await db_async.get_token_balance(user["id"])
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
            await db_async.spend_tokens(user["id"], cost, "Immersive question" if is_immersive else "Chat message")
        new_balance = await db_async.get_token_balance(user["id"]) if user else None
        return {"response": result, "token_cost": cost, "balance": new_balance}
    except Exception as e:
        return JSONResponse({"error": f"AI generation failed: {str(e)}"}, 500)
