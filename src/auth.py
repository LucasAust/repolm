"""
RepoLM — Email/Password Auth
Simple signup + login with bcrypt password hashing.
"""

import os
import hashlib
import hmac
import secrets
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
import db as database
import db_async

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


def _hash_password(password: str, salt: str = None) -> tuple:
    """Hash password with PBKDF2. Returns (hash, salt)."""
    if salt is None:
        salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return pw_hash, salt


async def get_current_user(request: Request) -> Optional[dict]:
    """Async version — runs DB lookup in executor."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return await db_async.get_user_by_session(token)


def get_current_user_sync(request: Request) -> Optional[dict]:
    """Sync version for use in non-async contexts (background threads)."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return database.get_user_by_session(token)


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

    # Check if email exists
    def _check_existing():
        with database.db() as conn:
            return conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()

    existing = await db_async.execute_raw(_check_existing)
    if existing:
        return JSONResponse({"error": "Account already exists. Try logging in."}, 409)

    pw_hash, salt = _hash_password(password)

    # Check for referral code
    ref_code = body.get("referral_code", "").strip()
    referrer = await db_async.get_user_by_referral(ref_code) if ref_code else None

    signup_tokens = 10
    if referrer:
        signup_tokens = 15  # Extra 5 for referred users

    def _create_user():
        with database.db() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash, password_salt) VALUES (?,?,?,?)",
                (username, email, pw_hash, salt)
            )
            user_id = cur.lastrowid
            conn.execute("UPDATE users SET tokens = ? WHERE id=?", (signup_tokens, user_id))
            conn.execute(
                "INSERT INTO token_transactions (user_id, amount, action, description) VALUES (?,?,?,?)",
                (user_id, signup_tokens, "bonus", "Welcome bonus" + (" (referral)" if referrer else ""))
            )
            return user_id

    user_id = await db_async.execute_raw(_create_user)

    # Handle referral rewards
    if referrer:
        await db_async.set_referred_by(user_id, referrer["id"])
        await db_async.add_tokens(referrer["id"], 5, f"Referral reward: {username} signed up")

    # Send welcome email (async, fire-and-forget)
    try:
        from email_service import send_welcome
        if email:
            import threading
            threading.Thread(target=send_welcome, args=(email, username), daemon=True).start()
    except Exception:
        pass

    session_token = await db_async.create_session(user_id)
    response = JSONResponse({"ok": True, "username": username})
    response.set_cookie(SESSION_COOKIE, session_token, **_cookie_kwargs(request))
    return response


@router.post("/auth/login")
async def login(request: Request):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        return JSONResponse({"error": "Email and password required"}, 400)

    def _lookup():
        with database.db() as conn:
            return conn.execute(
                "SELECT id, username, password_hash, password_salt FROM users WHERE email=?", (email,)
            ).fetchone()

    row = await db_async.execute_raw(_lookup)

    if not row:
        return JSONResponse({"error": "Invalid email or password"}, 401)

    pw_hash, _ = _hash_password(password, row["password_salt"])
    if not hmac.compare_digest(pw_hash, row["password_hash"]):
        return JSONResponse({"error": "Invalid email or password"}, 401)

    session_token = await db_async.create_session(row["id"])
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
    user = await get_current_user(request)
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
                     "tokens": tokens, "has_purchased": purchased}}


@router.get("/auth/token")
async def get_token(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    user = await get_current_user(request)
    if not user or not token:
        return JSONResponse({"error": "Not authenticated"}, 401)
    return {"token": token}
