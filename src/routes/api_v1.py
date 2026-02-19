"""
RepoLM — Public API v1 endpoints.
Authenticated via X-API-Key header. Rate-limited per tier.
Uses thread pools instead of raw threads.
"""

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from pathlib import Path

import db as database
import state
from config import TOKEN_COSTS, TIER_RATE_LIMITS
from routes.repo import run_ingest
from concurrency import ingest_queue, generate_queue
import analytics

router = APIRouter(prefix="/api/v1")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _get_api_user(request: Request):
    """Extract and validate API key from request. Returns (user, error_response)."""
    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        return None, JSONResponse({"error": "Missing X-API-Key header"}, 401)
    user = database.get_user_by_api_key(api_key)
    if not user:
        return None, JSONResponse({"error": "Invalid API key"}, 401)
    sub = database.get_subscription(user["id"])
    plan = "free"
    if sub and sub.get("subscription_status") == "active":
        plan = sub.get("plan", "free")
    tier = TIER_RATE_LIMITS.get(plan, TIER_RATE_LIMITS["free"])
    daily_limit = tier.get("api_calls_per_day", 10)
    if not database.check_api_rate_limit(user["id"], daily_limit):
        return None, JSONResponse({"error": f"API rate limit exceeded ({daily_limit} calls/day for {plan} tier)"}, 429)
    analytics.track("api_call", user_id=user["id"], data={"endpoint": request.url.path})
    database.track_api_usage(user["id"], api_key, request.url.path)
    return user, None


@router.post("/repos")
async def api_ingest_repo(request: Request):
    """Ingest a repository via API."""
    user, err = _get_api_user(request)
    if err:
        return err
    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "url required"}, 400)
    if not url.startswith("http"):
        url = "https://github.com/" + url

    cost = TOKEN_COSTS["ingest"]
    balance = database.get_token_balance(user["id"])
    if balance < cost:
        return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
    database.spend_tokens(user["id"], cost, "API: Ingest repo")

    repo_id = str(uuid.uuid4())[:8]
    state.repos.set(repo_id, {"status": "queued", "message": "Starting...", "files": [], "text": "", "data": {}})

    status, queue_pos = ingest_queue.submit(repo_id, run_ingest, repo_id, url)
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
    user, err = _get_api_user(request)
    if err:
        return err
    repo = state.get_repo_with_fallback(repo_id)
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
    user, err = _get_api_user(request)
    if err:
        return err
    body = await request.json()
    kind = body.get("kind", "overview")
    depth = body.get("depth", "high-level")
    expertise = body.get("expertise", "amateur")

    repo = state.get_repo_with_fallback(repo_id)
    if not repo or repo["status"] != "ready":
        return JSONResponse({"error": "Repo not ready"}, 400)

    cost = TOKEN_COSTS.get(kind, 10)
    balance = database.get_token_balance(user["id"])
    if balance < cost:
        return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
    database.spend_tokens(user["id"], cost, f"API: Generate {kind}")

    from routes.generate import run_generate
    job_id = str(uuid.uuid4())[:8]
    state.jobs.set(job_id, {"status": "queued", "message": "Starting...", "result": None})

    status, queue_pos = generate_queue.submit(job_id, run_generate, job_id, repo_id, kind, depth, expertise)
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
    user, err = _get_api_user(request)
    if err:
        return err
    job = state.jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Not found"}, 404)
    return job


@router.get("/usage")
async def api_usage(request: Request):
    user, err = _get_api_user(request)
    if err:
        return err
    return database.get_api_usage_stats(user["id"])
