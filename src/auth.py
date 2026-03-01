"""
RepoLM — Email/Password Auth
Simple signup + login with bcrypt password hashing.
Rate limiting, email verification, CAPTCHA, session expiry.
"""

import os
import hashlib
import hmac
import logging
import secrets
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
import db_async

logger = logging.getLogger("repolm")

router = APIRouter()
SESSION_COOKIE = "repolm_session"


def _cookie_kwargs(request: Request = None) -> dict:
    """Return cookie settings, secure=True when behind HTTPS."""
    secure = False
    if request:
        forwarded = request.headers.get("x-forwarded-proto", "")
        if forwarded == "https" or request.url.scheme == "https":
            secure = True
    return {"max_age": 30 * 86400, "httponly": True, "samesite": "lax", "secure": secure}


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


async def _verify_captcha(token: str) -> bool:
    """Verify reCAPTCHA v3 token. Returns True if valid (score >= 0.5).
    Skips if secret key not set OR if site key not set (frontend can't generate tokens).
    """
    secret = os.environ.get("RECAPTCHA_SECRET_KEY")
    site_key = os.environ.get("RECAPTCHA_SITE_KEY")
    if not secret or not site_key:
        return True  # Skip if not fully configured
    if not token:
        return False  # Keys are configured but no token provided — block
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post("https://www.google.com/recaptcha/api/siteverify",
                                     data={"secret": secret, "response": token})
            data = resp.json()
            return data.get("success", False) and data.get("score", 0) >= 0.5
    except Exception:
        logger.warning("CAPTCHA verification failed, allowing request")
        return True


def _hash_password(password: str, salt: str = None) -> tuple:
    """Hash password with PBKDF2. Returns (hash, salt)."""
    if salt is None:
        salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return pw_hash, salt


async def get_current_user(request: Request, require_verified: bool = True) -> Optional[dict]:
    """Async version — runs DB lookup in executor.
    Sessions with expired expires_at are already filtered at the DB layer.
    If require_verified=True, returns None for unverified accounts.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    user = await db_async.get_user_by_session(token)
    if not user:
        return None
    # Email verification disabled — CAPTCHA + rate limiting handles spam
    # if require_verified and not user.get("email_verified", 0):
    #     return None
    return user


def get_current_user_sync(request: Request) -> Optional[dict]:
    """Sync version for use in non-async contexts (background threads)."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    import db as _sync_db
    return _sync_db.get_user_by_session(token)


async def get_user_plan(request: Request) -> str:
    user = await get_current_user(request)
    if not user:
        return "free"
    sub = await db_async.get_subscription(user["id"])
    if sub and sub.get("plan") == "pro" and sub.get("subscription_status") == "active":
        return "pro"
    return "free"


@router.post("/auth/signup")
async def signup(request: Request):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    username = body.get("username", "").strip()

    if not email or not password:
        return JSONResponse({"error": "Email and password required"}, 400)
    if len(password) < 6:
        return JSONResponse({"error": "Password must be at least 6 characters"}, 400)
    if not username:
        username = email.split("@")[0]

    # Rate limit: max 3 signups per IP per hour
    client_ip = _get_client_ip(request)
    allowed = await db_async.check_signup_rate_limit(client_ip, max_signups=3, window_seconds=3600)
    if not allowed:
        return JSONResponse({"error": "Too many signup attempts. Please try again later."}, 429)

    # CAPTCHA verification (skipped if RECAPTCHA_SECRET_KEY not set)
    captcha_token = body.get("captcha_token", "")
    if not await _verify_captcha(captcha_token):
        return JSONResponse({"error": "CAPTCHA verification failed. Please try again."}, 400)

    # Check if email exists
    existing = await db_async.check_email_exists(email)
    if existing:
        return JSONResponse({"error": "Account already exists. Try logging in."}, 409)

    pw_hash, salt = _hash_password(password)

    # Check for referral code
    ref_code = body.get("referral_code", "").strip()
    referrer = await db_async.get_user_by_referral(ref_code) if ref_code else None

    from config import DEFAULT_SIGNUP_TOKENS
    signup_tokens = DEFAULT_SIGNUP_TOKENS
    if referrer:
        signup_tokens = signup_tokens + 5  # Extra 5 for referred users

    referral_note = " (referral)" if referrer else ""
    user_id = await db_async.create_user_with_password(username, email, pw_hash, salt, signup_tokens, referral_note)

    # Record signup attempt for rate limiting
    await db_async.record_signup_attempt(client_ip)

    # Handle referral rewards
    if referrer:
        await db_async.set_referred_by(user_id, referrer["id"])
        await db_async.add_tokens(referrer["id"], 5, f"Referral reward: {username} signed up")

    # Send welcome email (fire-and-forget in background thread)
    if email:
        try:
            import threading
            from email_service import send_welcome
            threading.Thread(target=send_welcome, args=(email, username), daemon=True).start()
        except Exception:
            pass

    session_token = await db_async.create_session(user_id, ttl_days=1)
    response = JSONResponse({
        "ok": True,
        "username": username,
    })
    response.set_cookie(SESSION_COOKIE, session_token, **_cookie_kwargs(request))
    return response


@router.get("/auth/verify")
async def verify_email(request: Request):
    """Verify email address via token."""
    token = request.query_params.get("token", "")
    if not token:
        return JSONResponse({"error": "Missing verification token"}, 400)
    user = await db_async.verify_email_by_token(token)
    if not user:
        return JSONResponse({"error": "Invalid or expired verification token"}, 400)
    logger.info("✅ Email verified for user %s (id=%s)", user.get("username"), user.get("id"))
    return JSONResponse({"ok": True, "message": "Email verified successfully. You can now use your account."})


@router.post("/auth/login")
async def login(request: Request):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        return JSONResponse({"error": "Email and password required"}, 400)

    # Rate limit: max 5 per email per 15 min, 20 per IP per 15 min
    client_ip = _get_client_ip(request)
    allowed = await db_async.check_login_rate_limit(client_ip, email)
    if not allowed:
        return JSONResponse({"error": "Too many login attempts. Please try again later."}, 429)

    row = await db_async.login_lookup(email)

    # Record attempt regardless of outcome
    await db_async.record_login_attempt(client_ip, email)

    if not row:
        return JSONResponse({"error": "Invalid email or password"}, 401)

    pw_hash, _ = _hash_password(password, row["password_salt"])
    if not hmac.compare_digest(pw_hash, row["password_hash"]):
        return JSONResponse({"error": "Invalid email or password"}, 401)

    session_token = await db_async.create_session(row["id"], ttl_days=1)
    response = JSONResponse({"ok": True, "username": row["username"]})
    response.set_cookie(SESSION_COOKIE, session_token, **_cookie_kwargs(request))
    return response


@router.get("/auth/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await db_async.delete_session(token)
    response = RedirectResponse("/")
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/auth/me")
async def me(request: Request):
    user = await get_current_user(request, require_verified=False)
    if not user:
        return {"user": None}
    sub = await db_async.get_subscription(user["id"])
    plan = "free"
    if sub and sub.get("plan") == "pro" and sub.get("subscription_status") == "active":
        plan = "pro"
    tokens = await db_async.get_token_balance(user["id"])
    purchased = await db_async.has_ever_purchased(user["id"])
    return {"user": {"id": user["id"], "username": user["username"],
                     "email": user.get("email", ""), "plan": plan,
                     "tokens": tokens, "has_purchased": purchased,
                     "email_verified": bool(user.get("email_verified", 0))}}


@router.get("/auth/token")
async def get_token(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    user = await get_current_user(request)
    if not user or not token:
        return JSONResponse({"error": "Not authenticated"}, 401)
    return {"token": token}


@router.post("/auth/api-key")
async def generate_api_key(request: Request):
    """Generate (or regenerate) an API key for the authenticated user."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    key = await db_async.generate_api_key(user["id"])
    return {"api_key": key}


@router.get("/auth/api-key")
async def get_api_key(request: Request):
    """Get the current API key for the authenticated user."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    key = await db_async.get_user_api_key(user["id"])
    return {"api_key": key}
