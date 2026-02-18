"""
RepoLM — GitHub OAuth
Minimal: redirect to GitHub → callback → session cookie.
Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET env vars.
"""

import os
from typing import Optional
import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from db import create_or_update_user, create_session, get_user_by_session, delete_session

router = APIRouter()

GH_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GH_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GH_REDIRECT_URI = os.environ.get("REPOLM_URL", "http://127.0.0.1:8000") + "/auth/callback"

SESSION_COOKIE = "repolm_session"


def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return get_user_by_session(token)


@router.get("/auth/login")
async def login():
    if not GH_CLIENT_ID:
        return RedirectResponse("/app?auth=demo")
    return RedirectResponse(
        f"https://github.com/login/oauth/authorize?client_id={GH_CLIENT_ID}&redirect_uri={GH_REDIRECT_URI}&scope=read:user,user:email"
    )


@router.get("/auth/callback")
async def callback(code: str, request: Request):
    # Exchange code for token
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://github.com/login/oauth/access_token", json={
            "client_id": GH_CLIENT_ID,
            "client_secret": GH_CLIENT_SECRET,
            "code": code,
        }, headers={"Accept": "application/json"})
        data = resp.json()
        access_token = data.get("access_token")
        if not access_token:
            return RedirectResponse("/?error=auth_failed")

        # Get user info
        user_resp = await client.get("https://api.github.com/user",
                                     headers={"Authorization": f"Bearer {access_token}"})
        user_data = user_resp.json()

        # Get email
        email = user_data.get("email")
        if not email:
            email_resp = await client.get("https://api.github.com/user/emails",
                                          headers={"Authorization": f"Bearer {access_token}"})
            emails = email_resp.json()
            if emails and isinstance(emails, list):
                primary = next((e for e in emails if e.get("primary")), emails[0])
                email = primary.get("email")

    user_id = create_or_update_user(
        github_id=user_data["id"],
        username=user_data["login"],
        email=email,
        avatar_url=user_data.get("avatar_url"),
    )
    session_token = create_session(user_id)

    response = RedirectResponse("/app")
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
    return {"user": {"id": user["id"], "username": user["username"],
                     "avatar_url": user["avatar_url"]}}
