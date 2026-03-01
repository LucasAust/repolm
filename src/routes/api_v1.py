"""
RepoLM — Public API v1 endpoints.
Authenticated via X-API-Key header. Rate-limited per tier.
Uses thread pools instead of raw threads.
"""

import uuid

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
    return """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RepoLM API Docs</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{background:#0a0a0a;color:#e5e7eb;font-family:system-ui,-apple-system,sans-serif;padding:2rem;max-width:800px;margin:0 auto;line-height:1.6}
h1{color:#c084fc;margin-bottom:.5rem}h2{color:#a78bfa;margin:2rem 0 .5rem;font-size:1.2rem}h3{color:#e5e7eb;margin:1.5rem 0 .25rem;font-size:1rem}
p,li{color:#9ca3af;font-size:.9rem}a{color:#c084fc}code{background:#1f2937;padding:.15rem .4rem;border-radius:4px;font-size:.85rem;color:#34d399}
pre{background:#111827;border:1px solid #374151;border-radius:8px;padding:1rem;overflow-x:auto;margin:.5rem 0 1rem;font-size:.85rem;color:#d1d5db}
.method{display:inline-block;font-weight:700;font-size:.75rem;padding:.15rem .5rem;border-radius:4px;margin-right:.5rem}
.post{background:#7c3aed;color:#fff}.get{background:#059669;color:#fff}
.endpoint{font-family:monospace;color:#e5e7eb;font-size:.95rem}
hr{border:none;border-top:1px solid #1f2937;margin:2rem 0}
</style></head><body>
<h1>RepoLM API v1</h1>
<p>Programmatic access to repo ingestion, generation, and analysis.</p>
<p style="margin-top:.5rem">Base URL: <code>https://repolm.com/api/v1</code></p>

<h2>Authentication</h2>
<p>All endpoints require an <code>X-API-Key</code> header. Generate a key from your account settings (avatar menu → API Key).</p>
<pre>curl -H "X-API-Key: your_key_here" https://repolm.com/api/v1/usage</pre>

<hr>
<h2>Endpoints</h2>

<h3><span class="method post">POST</span><span class="endpoint">/repos</span></h3>
<p>Ingest a GitHub repository.</p>
<pre>{
  "url": "https://github.com/owner/repo"
}</pre>
<p>Returns: <code>{"repo_id": "abc123", "token_cost": 10}</code>. If queued: includes <code>"queued": true, "queue_position": N</code>.</p>

<h3><span class="method get">GET</span><span class="endpoint">/repos/{repo_id}</span></h3>
<p>Check repo ingestion status.</p>
<p>Returns: <code>{"status": "ready|queued|processing|error", "data": {...}, "file_count": N}</code></p>

<h3><span class="method post">POST</span><span class="endpoint">/repos/{repo_id}/generate</span></h3>
<p>Generate content from an ingested repo.</p>
<pre>{
  "kind": "overview",
  "depth": "high-level",
  "expertise": "amateur"
}</pre>
<p><code>kind</code>: <code>overview</code>, <code>podcast</code>, or <code>slides</code>.<br>
<code>depth</code>: <code>high-level</code> or <code>in-depth</code>.<br>
<code>expertise</code>: <code>amateur</code>, <code>intermediate</code>, or <code>expert</code>.</p>
<p>Returns: <code>{"job_id": "xyz789", "token_cost": 25}</code></p>

<h3><span class="method get">GET</span><span class="endpoint">/jobs/{job_id}</span></h3>
<p>Poll job status. Returns the job object with <code>status</code>, <code>message</code>, and <code>result</code> when complete.</p>

<h3><span class="method get">GET</span><span class="endpoint">/usage</span></h3>
<p>Get your API usage stats.</p>

<hr>
<h2>Rate Limits</h2>
<ul><li>Free: 10 API calls/day</li><li>Pro: 100 API calls/day</li><li>Enterprise: 1,000 API calls/day</li></ul>

<h2>Token Costs</h2>
<ul><li>Ingest: 10 tokens</li><li>Overview: 25 tokens</li><li>Podcast: 50 tokens</li><li>Slides: 25 tokens</li></ul>

<p style="margin-top:2rem;color:#6b7280;font-size:.8rem">Questions? <a href="mailto:support@repolm.com">support@repolm.com</a></p>
</body></html>"""


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

    cost = TOKEN_COSTS["ingest"]
    balance = await db_async.get_token_balance(user["id"])
    if balance < cost:
        return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
    await db_async.spend_tokens(user["id"], cost, "API: Ingest repo")

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
