"""
RepoLM — FastAPI application setup, middleware, and router includes.
"""

import asyncio
import logging
import os
import signal
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from config import CARBON_SERVE, CARBON_PLACEMENT, ALLOWED_ORIGINS, validate_config
from auth import router as auth_router
from payments import router as payments_router
from routes.repo import router as repo_router
from routes.generate import router as generate_router
from routes.audio import router as audio_router
from routes.slides import router as slides_router
from routes.share import router as share_router
from routes.learn import router as learn_router
from routes.lab import router as lab_router
from routes.examples import router as examples_router
from routes.admin import router as admin_router
from routes.api_v1 import router as api_v1_router
from routes.referral import router as referral_router
from routes.seo import router as seo_router
import state
import concurrency

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("repolm")

TEMPLATES_DIR = Path(__file__).parent / "templates"
APP_VERSION = "1.1.0"
_start_time = time.time()

MAX_REQUEST_BODY = 50 * 1024 * 1024  # 50MB
REQUEST_TIMEOUT = 300  # 5 minutes


# ── Lifespan ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_config()
    logger.info("RepoLM %s starting | DB: %s | PID: %d", APP_VERSION, os.environ.get("DATABASE_URL", "sqlite (local)"), os.getpid())

    # Initialize PostgreSQL pool if DATABASE_URL is set
    if os.environ.get("DATABASE_URL"):
        try:
            import db_postgres
            await db_postgres.init_pool()
            logger.info("PostgreSQL backend initialized")
        except Exception:
            logger.exception("Failed to initialize PostgreSQL — falling back to SQLite")

    # Initialize Redis if REDIS_URL is set
    try:
        import redis_client
        await redis_client.init_redis()
    except Exception:
        logger.exception("Failed to initialize Redis — continuing without cache")

    cleanup_task = asyncio.create_task(state.cleanup_stores())
    yield
    logger.info("RepoLM shutting down gracefully...")
    cleanup_task.cancel()
    concurrency.shutdown_pools()

    # Close Postgres pool
    if os.environ.get("DATABASE_URL"):
        try:
            import db_postgres
            await db_postgres.close_pool()
        except Exception:
            pass

    # Close Redis
    try:
        import redis_client
        await redis_client.close_redis()
    except Exception:
        pass

    # Close PostgreSQL pool
    if os.environ.get("DATABASE_URL"):
        try:
            import db_postgres
            await db_postgres.close_pool()
        except Exception:
            pass

    # Close Redis
    try:
        import redis_client
        await redis_client.close_redis()
    except Exception:
        pass

    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="RepoLM", version=APP_VERSION, lifespan=lifespan)

# ── Static Files ──
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Pure ASGI Middleware ──
# NOTE: We avoid BaseHTTPMiddleware entirely because it wraps StreamingResponse
# bodies through background tasks + memory channels, which breaks SSE streaming
# after the first request completes (known Starlette issue in <= 0.27).


class SecurityHeadersMiddleware:
    """Add security headers to all responses (pure ASGI, no body wrapping)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"x-xss-protection", b"1; mode=block"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                ]
                scheme = scope.get("scheme", "http")
                if scheme == "https":
                    extra.append((b"strict-transport-security", b"max-age=31536000"))
                message = {**message, "headers": list(message.get("headers", [])) + extra}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestSizeLimitMiddleware:
    """Reject requests with Content-Length > MAX_REQUEST_BODY (pure ASGI)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length and int(content_length) > MAX_REQUEST_BODY:
            response = JSONResponse({"error": "Request body too large (max 50MB)"}, 413)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class RequestLoggingMiddleware:
    """Log requests with timing and request ID (pure ASGI)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())[:8]
        start = time.time()
        path = scope.get("path", "")
        method = scope.get("method", "")
        status_code = 0

        async def send_with_logging(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
                extra_headers = [(b"x-request-id", request_id.encode())]
                message = {**message, "headers": list(message.get("headers", [])) + extra_headers}
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                elapsed = (time.time() - start) * 1000
                if not path.startswith("/static"):
                    logger.info("%s %s %d %.0fms [%s]", method, path, status_code, elapsed, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_logging)
        except Exception:
            elapsed = (time.time() - start) * 1000
            logger.exception("Unhandled error %s %s %.0fms [%s]", method, path, elapsed, request_id)
            response = JSONResponse({"error": "Internal server error", "request_id": request_id}, 500)
            await response(scope, receive, send)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──
app.include_router(auth_router)
app.include_router(payments_router)
app.include_router(repo_router)
app.include_router(generate_router)
app.include_router(audio_router)
app.include_router(slides_router)
app.include_router(share_router)
app.include_router(learn_router)
app.include_router(lab_router)
app.include_router(examples_router)
app.include_router(admin_router)
app.include_router(api_v1_router)
app.include_router(referral_router)
app.include_router(seo_router)


# ── HTTP Error Handlers ──
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        try:
            html = TEMPLATES_DIR.joinpath("404.html").read_text()
            return HTMLResponse(html, status_code=404)
        except Exception:
            pass
    if exc.status_code == 500:
        try:
            html = TEMPLATES_DIR.joinpath("500.html").read_text()
            return HTMLResponse(html, status_code=500)
        except Exception:
            pass
    return JSONResponse({"error": exc.detail}, exc.status_code)


# ── Global Exception Handler ──
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    try:
        html = TEMPLATES_DIR.joinpath("500.html").read_text()
        return HTMLResponse(html, status_code=500)
    except Exception:
        return JSONResponse({"error": "Internal server error"}, 500)


# ── Health Endpoints ──
# Dedicated pool for health checks — NEVER competes with DB or LLM threads
from concurrent.futures import ThreadPoolExecutor as _TP
_health_pool = _TP(max_workers=2, thread_name_prefix="health")


@app.get("/health")
async def health():
    """Fast health check. Must NEVER block the DB pool."""
    return {
        "status": "ok",
        "version": APP_VERSION,
        "uptime": round(time.time() - _start_time, 1),
        "pressure": "low",
    }


@app.get("/ready")
async def ready():
    return {"status": "ready"}


@app.get("/api/status")
async def api_status():
    """Pool utilization endpoint for monitoring."""
    pools = concurrency.get_pool_status()
    from services.llm import get_circuit_stats
    return {
        "pools": pools,
        "circuit_breaker": get_circuit_stats(),
    }


# ── Page Routes ──
@app.get("/", response_class=HTMLResponse)
async def landing():
    html = TEMPLATES_DIR.joinpath("landing.html").read_text()
    html = html.replace("__CARBON_SERVE__", CARBON_SERVE)
    html = html.replace("__CARBON_PLACEMENT__", CARBON_PLACEMENT)
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/app", response_class=HTMLResponse)
async def app_page():
    html = TEMPLATES_DIR.joinpath("app.html").read_text()
    html = html.replace("__CARBON_SERVE__", CARBON_SERVE)
    html = html.replace("__CARBON_PLACEMENT__", CARBON_PLACEMENT)
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/developers", response_class=HTMLResponse)
async def developers_page():
    return HTMLResponse(TEMPLATES_DIR.joinpath("developers.html").read_text())


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page():
    return HTMLResponse(TEMPLATES_DIR.joinpath("pricing.html").read_text())


@app.get("/terms", response_class=HTMLResponse)
async def terms_page():
    return HTMLResponse(TEMPLATES_DIR.joinpath("terms.html").read_text())


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    return HTMLResponse(TEMPLATES_DIR.joinpath("privacy.html").read_text())


# ── Achievements & Email Prefs API ──

@app.get("/api/my/achievements")
async def get_achievements(request: Request):
    from auth import get_current_user
    import db_async
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    achievements = await db_async.get_user_achievements(user["id"])
    return {"achievements": achievements}


@app.get("/api/my/email-preferences")
async def get_email_prefs(request: Request):
    from auth import get_current_user
    import db_async
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    return await db_async.get_email_preferences(user["id"])


@app.post("/api/my/email-preferences")
async def update_email_prefs(request: Request):
    from auth import get_current_user
    import db_async
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    body = await request.json()
    await db_async.update_email_preferences(user["id"], **body)
    return {"ok": True}


@app.post("/api/share/track")
async def track_share(request: Request):
    """Track a social share."""
    import db_async
    body = await request.json()
    content_id = body.get("content_id", "")
    platform = body.get("platform", "link")
    if content_id:
        await db_async.increment_share_count(content_id, platform)
    return {"ok": True}
