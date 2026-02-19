"""
RepoLM — Admin endpoints: dashboard, stats, cache, analytics.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse

from config import API_KEY, ADMIN_API_KEY
import cache as content_cache
import analytics
import db as database

router = APIRouter()

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _require_admin(request: Request) -> bool:
    """Check admin API key. Returns True if unauthorized."""
    key = ADMIN_API_KEY or API_KEY
    if key:
        req_key = request.headers.get("x-api-key", "") or request.query_params.get("key", "")
        if req_key != key:
            return True
    return False


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard():
    """Admin dashboard page."""
    return HTMLResponse(TEMPLATES_DIR.joinpath("admin.html").read_text())


@router.get("/api/admin/stats")
async def admin_stats(request: Request):
    """Comprehensive admin stats from DB."""
    if _require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, 401)
    return database.get_admin_stats()


@router.get("/api/admin/cache-stats")
async def cache_stats(request: Request):
    if _require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, 401)
    return content_cache.get_cache_stats()


@router.post("/api/admin/cache-cleanup")
async def cache_cleanup(request: Request):
    if _require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, 401)
    deleted = content_cache.cleanup_expired()
    return {"deleted": deleted}


@router.get("/api/admin/analytics")
async def admin_analytics(request: Request):
    """Aggregated usage analytics. Requires admin API key."""
    if _require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, 401)
    days = int(request.query_params.get("days", "30"))
    return analytics.get_stats(days)
