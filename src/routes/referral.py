"""
RepoLM — Referral system endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from auth import get_current_user
import db as database
from config import REFERRAL_BONUS_REFERRER, REFERRAL_BONUS_REFEREE

router = APIRouter()


@router.get("/ref/{code}")
async def referral_redirect(code: str):
    """Redirect referral link to app with tracking."""
    user = database.get_user_by_referral(code)
    if not user:
        return RedirectResponse("/")
    return RedirectResponse(f"/app?ref={code}")


@router.get("/api/my/referral")
async def get_referral_info(request: Request):
    """Get the current user's referral code and stats."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    code = database.get_referral_code(user["id"])
    return {"referral_code": code, "referral_url": f"/ref/{code}"}


@router.post("/api/my/api-key")
async def generate_api_key(request: Request):
    """Generate or regenerate an API key for the user."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    key = database.generate_api_key(user["id"])
    return {"api_key": key}


@router.get("/api/my/api-key")
async def get_api_key(request: Request):
    """Get the current user's API key."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    return {"api_key": user.get("api_key")}


@router.get("/api/my/api-usage")
async def get_api_usage(request: Request):
    """Get API usage stats."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    return database.get_api_usage_stats(user["id"])
