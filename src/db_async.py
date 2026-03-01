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


from concurrent.futures import ThreadPoolExecutor
_db_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="db-async")

# Main event loop reference — set at startup, used by sync bridges in background threads
_main_loop = None  # type: Optional[asyncio.AbstractEventLoop]


def set_main_loop(loop):
    """Call once at app startup to store the main event loop for sync bridges."""
    global _main_loop
    _main_loop = loop


async def _run(fn, *args, **kwargs):
    """Run a sync function in a dedicated DB thread pool (never starved by LLM/other I/O)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_db_pool, partial(fn, *args, **kwargs))


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


async def get_user_api_key(user_id: int) -> Optional[str]:
    if _USE_POSTGRES:
        return await _pg.get_user_api_key(user_id)
    def _get():
        with _sync.db() as conn:
            row = conn.execute("SELECT api_key FROM users WHERE id=?", (user_id,)).fetchone()
            return row["api_key"] if row else None
    return await _run(_get)


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


# ── Repo Cache (memory → Redis → Postgres) ──

async def get_repo_with_fallback(repo_id):
    # type: (str) -> Optional[dict]
    """Load repo: memory → Redis → Postgres."""
    # 1. In-memory (fast, no I/O)
    repo = _state.repos.get(repo_id)
    if repo:
        return repo

    # 2. Redis
    try:
        import redis_client
        if redis_client.is_available():
            redis_repo = await redis_client.load_repo(repo_id)
            if redis_repo and redis_repo.get("status") == "ready":
                _state.repos.set(repo_id, redis_repo)
                return redis_repo
    except Exception:
        pass

    # 3. Postgres
    if _USE_POSTGRES:
        try:
            pg_repo = await _pg.load_repo(repo_id)
            if pg_repo and pg_repo.get("status") == "ready":
                _state.repos.set(repo_id, pg_repo)
                return pg_repo
        except Exception:
            logger.exception("Failed to load repo %s from Postgres", repo_id)

    return None


def sync_get_repo_with_fallback(repo_id):
    # type: (str) -> Optional[dict]
    """Sync bridge for background threads — memory → Postgres via main event loop."""
    repo = _state.repos.get(repo_id)
    if repo:
        return repo
    if _USE_POSTGRES and _main_loop and _main_loop.is_running():
        try:
            future = asyncio.run_coroutine_threadsafe(_pg.load_repo(repo_id), _main_loop)
            pg_repo = future.result(timeout=10)
            if pg_repo and pg_repo.get("status") == "ready":
                _state.repos.set(repo_id, pg_repo)
                return pg_repo
        except Exception:
            logger.exception("sync_get_repo_with_fallback failed for %s", repo_id)
    return None


async def find_cached_repo_by_url(url):
    # type: (str) -> Optional[str]
    if _USE_POSTGRES:
        try:
            return await _pg.find_cached_repo_by_url(url)
        except Exception:
            logger.exception("find_cached_repo_by_url failed")
    return None


async def cache_repo_to_db(repo_id, repo_data):
    # type: (str, dict) -> None
    """Cache repo to Postgres (and Redis)."""
    if _USE_POSTGRES:
        try:
            await _pg.cache_repo(repo_id, repo_data)
        except Exception:
            logger.exception("Failed to cache repo %s to Postgres", repo_id)
    # Also push to Redis
    try:
        import redis_client
        if redis_client.is_available():
            await redis_client.cache_repo(repo_id, repo_data)
    except Exception:
        pass


def sync_cache_repo_to_db(repo_id, repo_data):
    # type: (str, dict) -> None
    """Sync bridge for background threads — writes to Postgres and waits for completion."""
    if not _main_loop or not _main_loop.is_running():
        logger.warning("sync_cache_repo_to_db: no main loop available for %s", repo_id)
        return
    if _USE_POSTGRES:
        try:
            future = asyncio.run_coroutine_threadsafe(_pg.cache_repo(repo_id, repo_data), _main_loop)
            future.result(timeout=30)  # wait for Postgres write to complete
            logger.info("sync_cache_repo_to_db: cached %s to Postgres (%d files)", repo_id, len(repo_data.get("files", [])))
        except Exception:
            logger.exception("sync_cache_repo_to_db failed for %s", repo_id)
    try:
        import redis_client
        if redis_client.is_available():
            asyncio.run_coroutine_threadsafe(redis_client.cache_repo(repo_id, repo_data), _main_loop)
    except Exception:
        pass


# ── Auth (signup/login) ──

async def check_email_exists(email: str) -> Optional[dict]:
    if _USE_POSTGRES:
        return await _pg.check_email_exists(email)
    def _check():
        with _sync.db() as conn:
            row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            return dict(row) if row else None
    return await _run(_check)

async def create_user_with_password(username, email, pw_hash, salt, signup_tokens, referral_note=""):
    if _USE_POSTGRES:
        return await _pg.create_user_with_password(username, email, pw_hash, salt, signup_tokens, referral_note)
    def _create():
        with _sync.db() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash, password_salt) VALUES (?,?,?,?)",
                (username, email, pw_hash, salt))
            user_id = cur.lastrowid
            conn.execute("UPDATE users SET tokens = ? WHERE id=?", (signup_tokens, user_id))
            conn.execute(
                "INSERT INTO token_transactions (user_id, amount, action, description) VALUES (?,?,?,?)",
                (user_id, signup_tokens, "bonus", "Welcome bonus" + referral_note))
            return user_id
    return await _run(_create)

async def login_lookup(email: str) -> Optional[dict]:
    if _USE_POSTGRES:
        return await _pg.login_lookup(email)
    def _lookup():
        with _sync.db() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash, password_salt FROM users WHERE email=?", (email,)
            ).fetchone()
            return dict(row) if row else None
    return await _run(_lookup)

# ── Purchases ──

async def set_has_purchased(user_id: int):
    if _USE_POSTGRES:
        return await _pg.set_has_purchased(user_id)
    return await _run(_sync.set_has_purchased, user_id)

async def get_token_transactions(user_id: int, limit: int = 20) -> list:
    if _USE_POSTGRES:
        return await _pg.get_token_transactions(user_id, limit)
    return await _run(_sync.get_token_transactions, user_id, limit)

# ── Stripe ──

async def get_user_by_stripe_customer(customer_id: str) -> Optional[dict]:
    """Look up user by Stripe customer ID."""
    if _USE_POSTGRES:
        return await _pg.get_user_by_stripe_customer(customer_id)
    def _lookup():
        with _sync.db() as conn:
            row = conn.execute("SELECT id, plan FROM users WHERE stripe_customer_id=?", (customer_id,)).fetchone()
            return dict(row) if row else None
    return await _run(_lookup)

# ── Cleanup ──

async def cleanup_old_jobs(max_age_hours: int = 24):
    if _USE_POSTGRES:
        return await _pg.cleanup_old_jobs(max_age_hours)
    return await _run(_sync.cleanup_old_jobs, max_age_hours)

async def cleanup_rate_limits():
    if _USE_POSTGRES:
        pass  # Redis handles TTL-based cleanup
    else:
        return await _run(_sync.cleanup_rate_limits)

# ── Health ──

async def db_health_check() -> bool:
    """Check if DB is responsive."""
    if _USE_POSTGRES:
        try:
            pool = _pg._get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False
    def _check():
        with _sync.db() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    try:
        return await _run(_check)
    except Exception:
        return False

# ── Sync bridges for background threads ──

def sync_update_job(job_id, status=None, message=None, result=None):
    """Sync version for background thread workers. Uses sync SQLite."""
    _sync.update_job(job_id, status=status, message=message, result=result)

# ── Direct DB access (for raw queries) ──

async def execute_raw(fn):
    """Run a function that uses db() context manager in executor."""
    return await _run(fn)
