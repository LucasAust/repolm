"""
RepoLM — Shared route helpers: rate limiting, SSE formatting, auth checks.
"""

import json
import time

from fastapi import Request

from config import API_KEY, RATE_LIMITS, USER_RATE_LIMITS
from auth import get_current_user
import db_async


async def is_pro_user(request: Request) -> bool:
    """Check if the current user has an active Pro or Team subscription."""
    user = await get_current_user(request)
    if not user:
        return False
    sub = await db_async.get_subscription(user["id"])
    if not sub:
        return False
    return sub.get("plan") in ("pro", "team") and sub.get("subscription_status") == "active"


async def check_rate_limit(request: Request, action: str) -> bool:
    """Returns True if rate limited. Pro users bypass. Auth users get higher limits.
    Uses SQLite-backed rate limiting for multi-worker safety."""
    if API_KEY:
        req_key = request.headers.get("x-api-key", "")
        if req_key == API_KEY:
            return False
    if await is_pro_user(request):
        return False
    user = await get_current_user(request)
    if user:
        key = "{}:user:{}".format(action, user["id"])
        limits = USER_RATE_LIMITS.get(action, RATE_LIMITS.get(action))
    else:
        ip = request.client.host
        key = "{}:{}".format(action, ip)
        limits = RATE_LIMITS.get(action)
    if not limits:
        return False
    return await db_async.check_rate_limit_db(key, limits["max"], limits["window"])


async def get_rate_limit_headers(request: Request, action: str) -> dict:
    """Get rate limit headers for a response."""
    user = await get_current_user(request)
    if user:
        limits = USER_RATE_LIMITS.get(action, RATE_LIMITS.get(action))
    else:
        limits = RATE_LIMITS.get(action)
    if not limits:
        return {}
    return {
        "X-RateLimit-Limit": str(limits["max"]),
        "X-RateLimit-Window": str(limits["window"]),
        "X-RateLimit-Reset": str(int(time.time()) + limits["window"]),
    }


def sse_format(data: str, event: str = None) -> str:
    """Format a Server-Sent Event."""
    lines = []
    if event:
        lines.append("event: {}".format(event))
    lines.append("data: {}".format(json.dumps(data)))
    lines.append("")
    lines.append("")
    return "\n".join(lines)
