"""
RepoLM — Async database abstraction layer.

If DATABASE_URL is set → uses db_postgres.py (native async PostgreSQL via asyncpg).
Otherwise → falls back to db.py (sync SQLite) via run_in_executor.

Routes only import this module. Same function signatures regardless of backend.
"""

import asyncio
import os
import logging
from functools import partial
from typing import Optional

logger = logging.getLogger("repolm")

_USE_POSTGRES = bool(os.environ.get("DATABASE_URL"))

if _USE_POSTGRES:
    import db_postgres as _pg
    logger.info("db_async: PostgreSQL backend selected (DATABASE_URL set)")
else:
    logger.info("db_async: SQLite backend selected (no DATABASE_URL)")

import db as _sync
import state as _state


async def _run(fn, *args, **kwargs):
    """Run a sync function in the default executor (thread pool)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(fn, *args, **kwargs))


# ── Auth / Sessions ──

async def get_user_by_session(token: str) -> Optional[dict]:
    if _USE_POSTGRES:
        return await _pg.get_user_by_session(token)
    return await _run(_sync.get_user_by_session, token)


async def create_session(user_id: int, ttl_days: int = 30) -> str:
    if _USE_POSTGRES:
        return await _pg.create_session(user_id, ttl_days)
    return await _run(_sync.create_session, user_id, ttl_days)


async def delete_session(token: str):
    if _USE_POSTGRES:
        return await _pg.delete_session(token)
    return await _run(_sync.delete_session, token)


# ── Users ──

async def create_or_update_user(github_id, username, email=None, avatar_url=None):
    if _USE_POSTGRES:
        return await _pg.create_or_update_user(github_id, username, email, avatar_url)
    return await _run(_sync.create_or_update_user, github_id, username, email, avatar_url)


# ── Tokens ──

async def get_token_balance(user_id: int) -> int:
    if _USE_POSTGRES:
        return await _pg.get_token_balance(user_id)
    return await _run(_sync.get_token_balance, user_id)


async def spend_tokens(user_id: int, amount: int, description: str) -> bool:
    if _USE_POSTGRES:
        return await _pg.spend_tokens(user_id, amount, description)
    return await _run(_sync.spend_tokens, user_id, amount, description)


async def add_tokens(user_id: int, amount: int, description: str):
    if _USE_POSTGRES:
        return await _pg.add_tokens(user_id, amount, description)
    return await _run(_sync.add_tokens, user_id, amount, description)


async def has_ever_purchased(user_id: int) -> bool:
    if _USE_POSTGRES:
        return await _pg.has_ever_purchased(user_id)
    return await _run(_sync.has_ever_purchased, user_id)


# ── Subscriptions ──

async def get_subscription(user_id: int) -> Optional[dict]:
    if _USE_POSTGRES:
        return await _pg.get_subscription(user_id)
    return await _run(_sync.get_subscription, user_id)


async def update_subscription(user_id: int, **kwargs):
    if _USE_POSTGRES:
        return await _pg.update_subscription(user_id, **kwargs)
    return await _run(partial(_sync.update_subscription, user_id, **kwargs))


async def increment_repo_count(user_id: int):
    if _USE_POSTGRES:
        return await _pg.increment_repo_count(user_id)
    return await _run(_sync.increment_repo_count, user_id)


async def check_repo_limit(user_id: int) -> bool:
    if _USE_POSTGRES:
        return await _pg.check_repo_limit(user_id)
    return await _run(_sync.check_repo_limit, user_id)


# ── Repos ──

async def save_repo(user_id, url, name, tree, file_count, total_chars, languages, repo_text, file_index):
    if _USE_POSTGRES:
        return await _pg.save_repo(user_id, url, name, tree, file_count,
                                   total_chars, languages, repo_text, file_index)
    return await _run(_sync.save_repo, user_id, url, name, tree, file_count,
                      total_chars, languages, repo_text, file_index)


async def get_user_repos(user_id: int) -> list:
    if _USE_POSTGRES:
        return await _pg.get_user_repos(user_id)
    return await _run(_sync.get_user_repos, user_id)


async def get_repo(repo_id: int, user_id: int) -> Optional[dict]:
    if _USE_POSTGRES:
        return await _pg.get_repo(repo_id, user_id)
    return await _run(_sync.get_repo, repo_id, user_id)


async def delete_repo(repo_id: int, user_id: int):
    if _USE_POSTGRES:
        return await _pg.delete_repo(repo_id, user_id)
    return await _run(_sync.delete_repo, repo_id, user_id)


# ── Generated Content ──

async def save_generated(repo_id, kind, depth, expertise, content):
    if _USE_POSTGRES:
        return await _pg.save_generated(repo_id, kind, depth, expertise, content)
    return await _run(_sync.save_generated, repo_id, kind, depth, expertise, content)


async def get_generated(repo_id: int, kind: str = None) -> list:
    if _USE_POSTGRES:
        return await _pg.get_generated(repo_id, kind)
    return await _run(_sync.get_generated, repo_id, kind)


# ── Chats ──

async def save_chat(repo_id, role, message, selection=None, file_path=None):
    if _USE_POSTGRES:
        return await _pg.save_chat(repo_id, role, message, selection, file_path)
    return await _run(_sync.save_chat, repo_id, role, message, selection, file_path)


async def get_chats(repo_id: int, limit: int = 50) -> list:
    if _USE_POSTGRES:
        return await _pg.get_chats(repo_id, limit)
    return await _run(_sync.get_chats, repo_id, limit)


# ── Jobs ──

async def create_job(job_id, kind, repo_id=None, status="queued", message="Starting..."):
    if _USE_POSTGRES:
        return await _pg.create_job(job_id, kind, repo_id, status, message)
    return await _run(_sync.create_job, job_id, kind, repo_id, status, message)


async def update_job(job_id, status=None, message=None, result=None):
    if _USE_POSTGRES:
        return await _pg.update_job(job_id, status=status, message=message, result=result)
    return await _run(partial(_sync.update_job, job_id, status=status, message=message, result=result))


async def get_job(job_id: str) -> Optional[dict]:
    if _USE_POSTGRES:
        return await _pg.get_job(job_id)
    return await _run(_sync.get_job, job_id)


# ── Rate Limiting ──

async def check_rate_limit_db(key: str, max_requests: int, window_seconds: int) -> bool:
    if _USE_POSTGRES:
        return await _pg.check_rate_limit_db(key, max_requests, window_seconds)
    return await _run(_sync.check_rate_limit_db, key, max_requests, window_seconds)


# ── Anonymous Usage ──

async def check_anonymous_usage(ip: str) -> int:
    if _USE_POSTGRES:
        return await _pg.check_anonymous_usage(ip)
    return await _run(_sync.check_anonymous_usage, ip)


async def increment_anonymous_usage(ip: str):
    if _USE_POSTGRES:
        return await _pg.increment_anonymous_usage(ip)
    return await _run(_sync.increment_anonymous_usage, ip)


# ── Public Overviews / SEO ──

async def save_public_overview(owner, repo_name, repo_url, overview, **kwargs):
    if _USE_POSTGRES:
        return await _pg.save_public_overview(owner, repo_name, repo_url, overview, **kwargs)
    return await _run(partial(_sync.save_public_overview, owner, repo_name, repo_url, overview, **kwargs))


async def get_public_overview(owner: str, repo_name: str) -> Optional[dict]:
    if _USE_POSTGRES:
        return await _pg.get_public_overview(owner, repo_name)
    return await _run(_sync.get_public_overview, owner, repo_name)


async def list_public_overviews(limit: int = 1000) -> list:
    if _USE_POSTGRES:
        return await _pg.list_public_overviews(limit)
    return await _run(_sync.list_public_overviews, limit)


async def get_trending_repos(days: int = 7, limit: int = 10) -> list:
    if _USE_POSTGRES:
        return await _pg.get_trending_repos(days, limit)
    return await _run(_sync.get_trending_repos, days, limit)


# ── Achievements ──

async def grant_achievement(user_id: int, badge: str) -> bool:
    if _USE_POSTGRES:
        return await _pg.grant_achievement(user_id, badge)
    return await _run(_sync.grant_achievement, user_id, badge)


async def get_user_achievements(user_id: int) -> list:
    if _USE_POSTGRES:
        return await _pg.get_user_achievements(user_id)
    return await _run(_sync.get_user_achievements, user_id)


# ── Referrals ──

async def get_referral_code(user_id: int) -> Optional[str]:
    if _USE_POSTGRES:
        return await _pg.get_referral_code(user_id)
    return await _run(_sync.get_referral_code, user_id)


async def get_user_by_referral(code: str) -> Optional[dict]:
    if _USE_POSTGRES:
        return await _pg.get_user_by_referral(code)
    return await _run(_sync.get_user_by_referral, code)


async def set_referred_by(user_id: int, referrer_id: int):
    if _USE_POSTGRES:
        return await _pg.set_referred_by(user_id, referrer_id)
    return await _run(_sync.set_referred_by, user_id, referrer_id)


# ── API Keys ──

async def generate_api_key(user_id: int) -> str:
    if _USE_POSTGRES:
        return await _pg.generate_api_key(user_id)
    return await _run(_sync.generate_api_key, user_id)


async def get_user_by_api_key(api_key: str) -> Optional[dict]:
    if _USE_POSTGRES:
        return await _pg.get_user_by_api_key(api_key)
    return await _run(_sync.get_user_by_api_key, api_key)


async def track_api_usage(user_id, api_key, endpoint, tokens_used=0):
    if _USE_POSTGRES:
        return await _pg.track_api_usage(user_id, api_key, endpoint, tokens_used)
    return await _run(_sync.track_api_usage, user_id, api_key, endpoint, tokens_used)


async def check_api_rate_limit(user_id: int, daily_limit: int) -> bool:
    if _USE_POSTGRES:
        return await _pg.check_api_rate_limit(user_id, daily_limit)
    return await _run(_sync.check_api_rate_limit, user_id, daily_limit)


async def get_api_usage_stats(user_id: int, days: int = 30) -> dict:
    if _USE_POSTGRES:
        return await _pg.get_api_usage_stats(user_id, days)
    return await _run(_sync.get_api_usage_stats, user_id, days)


# ── Email Preferences ──

async def get_email_preferences(user_id: int) -> dict:
    if _USE_POSTGRES:
        return await _pg.get_email_preferences(user_id)
    return await _run(_sync.get_email_preferences, user_id)


async def update_email_preferences(user_id: int, **kwargs):
    if _USE_POSTGRES:
        return await _pg.update_email_preferences(user_id, **kwargs)
    return await _run(partial(_sync.update_email_preferences, user_id, **kwargs))


# ── Share Tracking ──

async def increment_share_count(content_id: str, platform: str = "link"):
    if _USE_POSTGRES:
        return await _pg.increment_share_count(content_id, platform)
    return await _run(_sync.increment_share_count, content_id, platform)


async def get_share_count(content_id: str) -> int:
    if _USE_POSTGRES:
        return await _pg.get_share_count(content_id)
    return await _run(_sync.get_share_count, content_id)


# ── Admin ──

async def get_db_stats() -> dict:
    if _USE_POSTGRES:
        return await _pg.get_db_stats()
    return await _run(_sync.get_db_stats)


async def get_admin_stats() -> dict:
    if _USE_POSTGRES:
        return await _pg.get_admin_stats()
    return await _run(_sync.get_admin_stats)


# ── State (repo cache) ──

async def get_repo_with_fallback(repo_id: str) -> Optional[dict]:
    # 1. In-memory (fast, no I/O)
    repo = _state.repos.get(repo_id)
    if repo:
        return repo

    # 2. Redis (native async, no executor needed)
    try:
        import redis_client
        if redis_client.is_available():
            redis_repo = await redis_client.load_repo(repo_id)
            if redis_repo and redis_repo.get("status") == "ready":
                _state.repos.set(repo_id, redis_repo)
                return redis_repo
    except Exception:
        pass

    # 3. SQLite cold storage (sync, needs executor)
    result = await _run(_state.get_repo_with_fallback, repo_id)
    return result


async def find_cached_repo_by_url(url: str) -> Optional[str]:
    return await _run(_state.find_cached_repo_by_url, url)


async def cache_repo_to_db(repo_id: str, repo_data: dict):
    return await _run(_state.cache_repo_to_db, repo_id, repo_data)


# ── Direct DB access (for signup/login raw queries) ──

async def execute_raw(fn):
    """Run a function that uses db() context manager in executor."""
    return await _run(fn)
