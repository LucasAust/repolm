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
from db import (create_session, get_user_by_session, delete_session,
                get_subscription, get_token_balance, has_ever_purchased)
import db as database

router = APIRouter()
SESSION_COOKIE = "repolm_session"


def _hash_password(password: str, salt: str = None) -> tuple:
    """Hash password with PBKDF2. Returns (hash, salt)."""
    if salt is None:
        salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return pw_hash, salt


def get_current_user(request: Request) -> Optional[dict]:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return get_user_by_session(token)


def get_user_plan(request: Request) -> str:
    user = get_current_user(request)
    if not user:
        return "free"
    sub = get_subscription(user["id"])
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
    with database.db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            return JSONResponse({"error": "Account already exists. Try logging in."}, 409)

    pw_hash, salt = _hash_password(password)

    with database.db() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, email, password_hash, password_salt) VALUES (?,?,?,?)",
            (username, email, pw_hash, salt)
        )
        user_id = cur.lastrowid
        # Give 10 free tokens
        conn.execute("UPDATE users SET tokens = 10 WHERE id=?", (user_id,))
        conn.execute(
            "INSERT INTO token_transactions (user_id, amount, action, description) VALUES (?,?,?,?)",
            (user_id, 10, "bonus", "Welcome bonus")
        )

    session_token = create_session(user_id)
    response = JSONResponse({"ok": True, "username": username})
    response.set_cookie(SESSION_COOKIE, session_token, max_age=30*86400, httponly=True, samesite="lax")
    return response


@router.post("/auth/login")
async def login(request: Request):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        return JSONResponse({"error": "Email and password required"}, 400)

    with database.db() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, password_salt FROM users WHERE email=?", (email,)
        ).fetchone()

    if not row:
        return JSONResponse({"error": "Invalid email or password"}, 401)

    pw_hash, _ = _hash_password(password, row["password_salt"])
    if not hmac.compare_digest(pw_hash, row["password_hash"]):
        return JSONResponse({"error": "Invalid email or password"}, 401)

    session_token = create_session(row["id"])
    response = JSONResponse({"ok": True, "username": row["username"]})
    response.set_cookie(SESSION_COOKIE, session_token, max_age=30*86400, httponly=True, samesite="lax")
    return response


@router.get("/auth/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(token)
    response = RedirectResponse("/")
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/auth/me")
async def me(request: Request):
    user = get_current_user(request)
    if not user:
        return {"user": None}
    sub = get_subscription(user["id"])
    plan = "free"
    if sub and sub.get("plan") == "pro" and sub.get("subscription_status") == "active":
        plan = "pro"
    tokens = get_token_balance(user["id"])
    purchased = has_ever_purchased(user["id"])
    return {"user": {"id": user["id"], "username": user["username"],
                     "email": user.get("email", ""), "plan": plan,
                     "tokens": tokens, "has_purchased": purchased}}


@router.get("/auth/token")
async def get_token(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    user = get_current_user(request)
    if not user or not token:
        return JSONResponse({"error": "Not authenticated"}, 401)
    return {"token": token}
