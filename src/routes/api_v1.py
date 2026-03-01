"""
RepoLM — Public API v1 endpoints.
Authenticated via X-API-Key header. Rate-limited per tier.
Uses thread pools instead of raw threads.
"""

import uuid
from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from pathlib import Path

import db_async
import state
from config import TOKEN_COSTS, TIER_RATE_LIMITS
from routes.repo import run_ingest
from concurrency import ingest_queue, generate_queue
import analytics

router = APIRouter(prefix="/api/v1")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


@router.get("/docs", response_class=HTMLResponse)
async def api_docs():
    template = TEMPLATES_DIR / "api_docs.html"
    return HTMLResponse(template.read_text(encoding="utf-8"))


async def _get_api_user(request: Request):
    """Extract and validate API key from request. Returns (user, error_response)."""
    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        return None, JSONResponse({"error": "Missing X-API-Key header"}, 401)
    user = await db_async.get_user_by_api_key(api_key)
    if not user:
        return None, JSONResponse({"error": "Invalid API key"}, 401)
    sub = await db_async.get_subscription(user["id"])
    plan = "free"
    if sub and sub.get("subscription_status") == "active":
        plan = sub.get("plan", "free")
    tier = TIER_RATE_LIMITS.get(plan, TIER_RATE_LIMITS["free"])
    daily_limit = tier.get("api_calls_per_day", 10)
    if not await db_async.check_api_rate_limit(user["id"], daily_limit):
        return None, JSONResponse({"error": f"API rate limit exceeded ({daily_limit} calls/day for {plan} tier)"}, 429)
    analytics.track("api_call", user_id=user["id"], data={"endpoint": request.url.path})
    await db_async.track_api_usage(user["id"], api_key, request.url.path)
    return user, None


@router.post("/repos")
async def api_ingest_repo(request: Request):
    """Ingest a repository via API."""
    user, err = await _get_api_user(request)
    if err:
        return err
    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "url required"}, 400)
    if not url.startswith("http"):
        url = "https://github.com/" + url

    webhook_url = body.get("webhook_url")
    api_key = request.headers.get("x-api-key", "")

    cost = TOKEN_COSTS["ingest"]
    balance = await db_async.get_token_balance(user["id"])
    if balance < cost:
        return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
    await db_async.spend_tokens(user["id"], cost, "API: Ingest repo")

    repo_id = str(uuid.uuid4())[:8]
    state.repos.set(repo_id, {"status": "queued", "message": "Starting...", "files": [], "text": "", "data": {}})

    _ingest_fn = partial(run_ingest, webhook_url=webhook_url, api_key=api_key)
    status, queue_pos = ingest_queue.submit(repo_id, _ingest_fn, repo_id, url)
    if status == "rejected":
        return JSONResponse({"error": "Server busy, try again in a moment"}, 503)

    analytics.track("repo_ingested", user_id=user["id"], data={"url": url})
    result = {"repo_id": repo_id, "token_cost": cost}
    if status == "queued":
        result["queued"] = True
        result["queue_position"] = queue_pos
    return result


@router.get("/repos/{repo_id}")
async def api_get_repo(repo_id: str, request: Request):
    user, err = await _get_api_user(request)
    if err:
        return err
    repo = await db_async.get_repo_with_fallback(repo_id)
    if not repo:
        return JSONResponse({"error": "Not found"}, 404)
    return {
        "status": repo["status"],
        "message": repo["message"],
        "data": repo.get("data", {}),
        "file_count": len(repo.get("files", [])),
    }


@router.post("/repos/{repo_id}/generate")
async def api_generate(repo_id: str, request: Request):
    user, err = await _get_api_user(request)
    if err:
        return err
    body = await request.json()
    kind = body.get("kind", "overview")
    depth = body.get("depth", "high-level")
    expertise = body.get("expertise", "amateur")

    webhook_url = body.get("webhook_url")
    api_key = request.headers.get("x-api-key", "")

    repo = await db_async.get_repo_with_fallback(repo_id)
    if not repo or repo["status"] != "ready":
        return JSONResponse({"error": "Repo not ready"}, 400)

    cost = TOKEN_COSTS.get(kind, 10)
    balance = await db_async.get_token_balance(user["id"])
    if balance < cost:
        return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
    await db_async.spend_tokens(user["id"], cost, f"API: Generate {kind}")

    from routes.generate import run_generate
    job_id = str(uuid.uuid4())[:8]
    state.jobs.set(job_id, {"status": "queued", "message": "Starting...", "result": None})

    _gen_fn = partial(run_generate, webhook_url=webhook_url, api_key=api_key)
    status, queue_pos = generate_queue.submit(job_id, _gen_fn, job_id, repo_id, kind, depth, expertise)
    if status == "rejected":
        return JSONResponse({"error": "Server busy, try again in a moment"}, 503)

    analytics.track("content_generated", user_id=user["id"], data={"kind": kind})
    result = {"job_id": job_id, "token_cost": cost}
    if status == "queued":
        result["queued"] = True
        result["queue_position"] = queue_pos
    return result


@router.get("/jobs/{job_id}")
async def api_get_job(job_id: str, request: Request):
    user, err = await _get_api_user(request)
    if err:
        return err
    job = state.jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Not found"}, 404)
    return job


@router.get("/usage")
async def api_usage(request: Request):
    user, err = await _get_api_user(request)
    if err:
        return err
    return await db_async.get_api_usage_stats(user["id"])


# ── Synchronous (blocking) endpoints ────────────────────────────────────────
# These wait until the job is done and return the result in one request.

import asyncio


async def _poll_repo_ready(repo_id: str, timeout: float = 120) -> dict:
    """Poll until repo is ready or error. Returns repo dict."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        repo = await db_async.get_repo_with_fallback(repo_id)
        if repo and repo.get("status") in ("ready", "error"):
            return repo
        await asyncio.sleep(1.5)
    return {"status": "timeout", "message": "Ingestion timed out"}


async def _poll_job_done(job_id: str, timeout: float = 180) -> dict:
    """Poll until job is done or error. Returns job dict."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        job = await db_async.get_job(job_id)
        if not job:
            job = state.jobs.get(job_id)
        if job and job.get("status") in ("done", "error"):
            return job
        await asyncio.sleep(1.5)
    return {"status": "timeout", "message": "Generation timed out", "result": None}


@router.post("/analyze")
async def api_analyze(request: Request):
    """One-shot endpoint: ingest a repo + generate content, return the result.
    Blocks until complete. Timeout ~5 min.

    Request body:
    {
        "url": "https://github.com/owner/repo",
        "kind": "overview",          // optional, default "overview"
        "depth": "high-level",       // optional
        "expertise": "amateur"       // optional
    }

    Returns the generated content directly.
    """
    user, err = await _get_api_user(request)
    if err:
        return err

    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "url required"}, 400)
    if not url.startswith("http"):
        url = "https://github.com/" + url

    kind = body.get("kind", "overview")
    depth = body.get("depth", "high-level")
    expertise = body.get("expertise", "amateur")

    # Check total cost upfront
    ingest_cost = TOKEN_COSTS["ingest"]
    gen_cost = TOKEN_COSTS.get(kind, 10)
    total_cost = ingest_cost + gen_cost
    balance = await db_async.get_token_balance(user["id"])
    if balance < total_cost:
        return JSONResponse({
            "error": "insufficient_tokens",
            "required": total_cost,
            "balance": balance,
            "breakdown": {"ingest": ingest_cost, "generate": gen_cost}
        }, 402)

    # Step 1: Ingest
    await db_async.spend_tokens(user["id"], ingest_cost, "API: Ingest repo")
    repo_id = str(uuid.uuid4())[:8]
    state.repos.set(repo_id, {"status": "queued", "message": "Starting...", "files": [], "text": "", "data": {}})
    status, _ = ingest_queue.submit(repo_id, run_ingest, repo_id, url)
    if status == "rejected":
        return JSONResponse({"error": "Server busy, try again in a moment"}, 503)

    repo = await _poll_repo_ready(repo_id, timeout=120)
    if repo.get("status") != "ready":
        return JSONResponse({
            "error": "ingestion_failed",
            "message": repo.get("message", "Ingestion failed or timed out")
        }, 500)

    # Step 2: Generate
    await db_async.spend_tokens(user["id"], gen_cost, f"API: Generate {kind}")
    from routes.generate import run_generate
    job_id = str(uuid.uuid4())[:8]
    await db_async.create_job(job_id, kind="generate", repo_id=repo_id)
    status, _ = generate_queue.submit(job_id, run_generate, job_id, repo_id, kind, depth, expertise)
    if status == "rejected":
        return JSONResponse({"error": "Server busy, try again in a moment"}, 503)

    job = await _poll_job_done(job_id, timeout=180)
    if job.get("status") != "done":
        return JSONResponse({
            "error": "generation_failed",
            "message": job.get("message", "Generation failed or timed out")
        }, 500)

    analytics.track("api_analyze", user_id=user["id"], data={"url": url, "kind": kind})
    return {
        "url": url,
        "kind": kind,
        "depth": depth,
        "expertise": expertise,
        "content": job.get("result", ""),
        "token_cost": total_cost,
    }
