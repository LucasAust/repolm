"""
RepoLM — Async wrappers for synchronous database operations.

Every function here runs its sync counterpart in a thread pool executor,
preventing SQLite I/O from blocking the asyncio event loop.

Phase 1 of the scaling plan: same SQLite backend, non-blocking access.
Phase 2 replaces this with asyncpg (Postgres) — same interface, new backend.
"""

import asyncio
from functools import partial
from typing import Optional

import db as _sync
import state as _state


async def _run(fn, *args, **kwargs):
    """Run a sync function in the default executor (thread pool)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(fn, *args, **kwargs))


# ── Auth / Sessions ──

async def get_user_by_session(token: str) -> Optional[dict]:
    return await _run(_sync.get_user_by_session, token)


async def create_session(user_id: int, ttl_days: int = 30) -> str:
    return await _run(_sync.create_session, user_id, ttl_days)


async def delete_session(token: str):
    return await _run(_sync.delete_session, token)


# ── Users ──

async def create_or_update_user(github_id, username, email=None, avatar_url=None):
    return await _run(_sync.create_or_update_user, github_id, username, email, avatar_url)


# ── Tokens ──

async def get_token_balance(user_id: int) -> int:
    return await _run(_sync.get_token_balance, user_id)


async def spend_tokens(user_id: int, amount: int, description: str) -> bool:
    return await _run(_sync.spend_tokens, user_id, amount, description)


async def add_tokens(user_id: int, amount: int, description: str):
    return await _run(_sync.add_tokens, user_id, amount, description)


async def has_ever_purchased(user_id: int) -> bool:
    return await _run(_sync.has_ever_purchased, user_id)


# ── Subscriptions ──

async def get_subscription(user_id: int) -> Optional[dict]:
    return await _run(_sync.get_subscription, user_id)


async def update_subscription(user_id: int, **kwargs):
    return await _run(partial(_sync.update_subscription, user_id, **kwargs))


async def increment_repo_count(user_id: int):
    return await _run(_sync.increment_repo_count, user_id)


async def check_repo_limit(user_id: int) -> bool:
    return await _run(_sync.check_repo_limit, user_id)


# ── Repos ──

async def save_repo(user_id, url, name, tree, file_count, total_chars, languages, repo_text, file_index):
    return await _run(_sync.save_repo, user_id, url, name, tree, file_count,
                      total_chars, languages, repo_text, file_index)


async def get_user_repos(user_id: int) -> list:
    return await _run(_sync.get_user_repos, user_id)


async def get_repo(repo_id: int, user_id: int) -> Optional[dict]:
    return await _run(_sync.get_repo, repo_id, user_id)


async def delete_repo(repo_id: int, user_id: int):
    return await _run(_sync.delete_repo, repo_id, user_id)


# ── Generated Content ──

async def save_generated(repo_id, kind, depth, expertise, content):
    return await _run(_sync.save_generated, repo_id, kind, depth, expertise, content)


async def get_generated(repo_id: int, kind: str = None) -> list:
    return await _run(_sync.get_generated, repo_id, kind)


# ── Chats ──

async def save_chat(repo_id, role, message, selection=None, file_path=None):
    return await _run(_sync.save_chat, repo_id, role, message, selection, file_path)


async def get_chats(repo_id: int, limit: int = 50) -> list:
    return await _run(_sync.get_chats, repo_id, limit)


# ── Jobs ──

async def create_job(job_id, kind, repo_id=None, status="queued", message="Starting..."):
    return await _run(_sync.create_job, job_id, kind, repo_id, status, message)


async def update_job(job_id, status=None, message=None, result=None):
    return await _run(partial(_sync.update_job, job_id, status=status, message=message, result=result))


async def get_job(job_id: str) -> Optional[dict]:
    return await _run(_sync.get_job, job_id)


# ── Rate Limiting ──

async def check_rate_limit_db(key: str, max_requests: int, window_seconds: int) -> bool:
    return await _run(_sync.check_rate_limit_db, key, max_requests, window_seconds)


# ── Anonymous Usage ──

async def check_anonymous_usage(ip: str) -> int:
    return await _run(_sync.check_anonymous_usage, ip)


async def increment_anonymous_usage(ip: str):
    return await _run(_sync.increment_anonymous_usage, ip)


# ── Public Overviews / SEO ──

async def save_public_overview(owner, repo_name, repo_url, overview, **kwargs):
    return await _run(partial(_sync.save_public_overview, owner, repo_name, repo_url, overview, **kwargs))


async def get_public_overview(owner: str, repo_name: str) -> Optional[dict]:
    return await _run(_sync.get_public_overview, owner, repo_name)


async def list_public_overviews(limit: int = 1000) -> list:
    return await _run(_sync.list_public_overviews, limit)


async def get_trending_repos(days: int = 7, limit: int = 10) -> list:
    return await _run(_sync.get_trending_repos, days, limit)


# ── Achievements ──

async def grant_achievement(user_id: int, badge: str) -> bool:
    return await _run(_sync.grant_achievement, user_id, badge)


async def get_user_achievements(user_id: int) -> list:
    return await _run(_sync.get_user_achievements, user_id)


# ── Referrals ──

async def get_referral_code(user_id: int) -> Optional[str]:
    return await _run(_sync.get_referral_code, user_id)


async def get_user_by_referral(code: str) -> Optional[dict]:
    return await _run(_sync.get_user_by_referral, code)


async def set_referred_by(user_id: int, referrer_id: int):
    return await _run(_sync.set_referred_by, user_id, referrer_id)


# ── API Keys ──

async def generate_api_key(user_id: int) -> str:
    return await _run(_sync.generate_api_key, user_id)


async def get_user_by_api_key(api_key: str) -> Optional[dict]:
    return await _run(_sync.get_user_by_api_key, api_key)


async def track_api_usage(user_id, api_key, endpoint, tokens_used=0):
    return await _run(_sync.track_api_usage, user_id, api_key, endpoint, tokens_used)


async def check_api_rate_limit(user_id: int, daily_limit: int) -> bool:
    return await _run(_sync.check_api_rate_limit, user_id, daily_limit)


async def get_api_usage_stats(user_id: int, days: int = 30) -> dict:
    return await _run(_sync.get_api_usage_stats, user_id, days)


# ── Email Preferences ──

async def get_email_preferences(user_id: int) -> dict:
    return await _run(_sync.get_email_preferences, user_id)


async def update_email_preferences(user_id: int, **kwargs):
    return await _run(partial(_sync.update_email_preferences, user_id, **kwargs))


# ── Share Tracking ──

async def increment_share_count(content_id: str, platform: str = "link"):
    return await _run(_sync.increment_share_count, content_id, platform)


async def get_share_count(content_id: str) -> int:
    return await _run(_sync.get_share_count, content_id)


# ── Admin ──

async def get_db_stats() -> dict:
    return await _run(_sync.get_db_stats)


async def get_admin_stats() -> dict:
    return await _run(_sync.get_admin_stats)


# ── State (SQLite repo cache) ──

async def get_repo_with_fallback(repo_id: str) -> Optional[dict]:
    return await _run(_state.get_repo_with_fallback, repo_id)


async def find_cached_repo_by_url(url: str) -> Optional[str]:
    return await _run(_state.find_cached_repo_by_url, url)


async def cache_repo_to_db(repo_id: str, repo_data: dict):
    return await _run(_state.cache_repo_to_db, repo_id, repo_data)


# ── Direct DB access (for signup/login raw queries) ──

async def execute_raw(fn):
    """Run a function that uses db() context manager in executor."""
    return await _run(fn)
