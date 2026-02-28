"""
RepoLM — Async PostgreSQL backend via asyncpg.
Drop-in replacement for db.py functions, but natively async.
Python 3.9 compatible.
"""

import asyncio
import json
import hashlib
import logging
import os
import secrets
import time
import zlib
from datetime import datetime
from typing import Optional, List, Dict

import asyncpg

logger = logging.getLogger("repolm")

_pool: Optional[asyncpg.Pool] = None


async def init_pool(database_url: str = None):
    """Create the connection pool. Call once at startup."""
    global _pool
    url = database_url or os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    # Railway sometimes gives postgres:// which asyncpg needs as postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    _pool = await asyncpg.create_pool(url, min_size=2, max_size=10, command_timeout=30)
    await _create_tables()
    logger.info("PostgreSQL pool initialized (min=2, max=10)")


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed")


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("PostgreSQL pool not initialized")
    return _pool


async def _create_tables():
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            github_id BIGINT UNIQUE,
            username TEXT NOT NULL,
            email TEXT,
            avatar_url TEXT,
            created_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
            last_login DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
            stripe_customer_id TEXT,
            subscription_status TEXT DEFAULT 'none',
            subscription_id TEXT,
            plan TEXT DEFAULT 'free',
            repos_this_month INTEGER DEFAULT 0,
            month_reset TEXT,
            tokens INTEGER DEFAULT 0,
            has_purchased INTEGER DEFAULT 0,
            password_hash TEXT,
            password_salt TEXT,
            referral_code TEXT,
            referred_by INTEGER,
            api_key TEXT,
            api_calls_today INTEGER DEFAULT 0,
            api_calls_date TEXT
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS course_packs (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            description TEXT,
            repo_url TEXT,
            price_cents INTEGER DEFAULT 2900,
            created_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS purchased_packs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            pack_id INTEGER NOT NULL REFERENCES course_packs(id),
            stripe_payment_id TEXT,
            purchased_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
            UNIQUE(user_id, pack_id)
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
            expires_at DOUBLE PRECISION
        )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS repos (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            url TEXT NOT NULL,
            name TEXT NOT NULL,
            repo_hash TEXT NOT NULL,
            tree TEXT,
            file_count INTEGER,
            total_chars INTEGER,
            languages TEXT,
            repo_text_z BYTEA,
            file_index TEXT,
            created_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
            last_accessed DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
            UNIQUE(user_id, repo_hash)
        )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_repos_user ON repos(user_id)")

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS generated (
            id SERIAL PRIMARY KEY,
            repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            depth TEXT NOT NULL,
            expertise TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
        )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_generated_repo ON generated(repo_id)")
        await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_generated_unique ON generated(repo_id, kind, depth, expertise)")

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id SERIAL PRIMARY KEY,
            repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            selection TEXT,
            file_path TEXT,
            created_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
        )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_chats_repo ON chats(repo_id)")

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS token_transactions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            amount INTEGER NOT NULL,
            action TEXT NOT NULL,
            description TEXT,
            created_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
        )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_token_tx_user ON token_transactions(user_id)")

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS job_status (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            message TEXT DEFAULT '',
            result TEXT,
            repo_id TEXT,
            created_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
            updated_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
        )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_job_status_repo ON job_status(repo_id)")

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT PRIMARY KEY,
            timestamps TEXT NOT NULL DEFAULT '[]',
            updated_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            api_key TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            tokens_used INTEGER DEFAULT 0,
            created_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
        )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_user ON api_usage(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_key ON api_usage(api_key)")

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS public_overviews (
            id SERIAL PRIMARY KEY,
            owner TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            repo_url TEXT NOT NULL,
            overview TEXT NOT NULL,
            description TEXT,
            languages TEXT,
            stars INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0,
            depth TEXT DEFAULT 'high-level',
            expertise TEXT DEFAULT 'amateur',
            created_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
            updated_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
            UNIQUE(owner, repo_name)
        )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_public_owner_repo ON public_overviews(owner, repo_name)")

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_achievements (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            badge TEXT NOT NULL,
            unlocked_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
            UNIQUE(user_id, badge)
        )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_achievements_user ON user_achievements(user_id)")

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS anonymous_usage (
            ip TEXT PRIMARY KEY,
            overviews_generated INTEGER DEFAULT 0,
            last_used DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS email_preferences (
            user_id INTEGER PRIMARY KEY REFERENCES users(id),
            welcome INTEGER DEFAULT 1,
            generation_ready INTEGER DEFAULT 1,
            weekly_digest INTEGER DEFAULT 1,
            marketing INTEGER DEFAULT 1,
            updated_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS share_counts (
            content_id TEXT PRIMARY KEY,
            platform TEXT,
            count INTEGER DEFAULT 0,
            last_shared DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
        )
        """)

    logger.info("PostgreSQL tables created/verified")


def _record_to_dict(record: asyncpg.Record) -> dict:
    return dict(record)


# ── Auth (signup/login raw queries) ──

async def check_email_exists(email: str) -> Optional[dict]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM users WHERE email=$1", email)
        return _record_to_dict(row) if row else None


async def create_user_with_password(username: str, email: str, pw_hash: str, salt: str, signup_tokens: int, referral_note: str = "") -> int:
    pool = _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            user_id = await conn.fetchval(
                "INSERT INTO users (username, email, password_hash, password_salt) VALUES ($1,$2,$3,$4) RETURNING id",
                username, email, pw_hash, salt)
            await conn.execute("UPDATE users SET tokens = $1 WHERE id=$2", signup_tokens, user_id)
            await conn.execute(
                "INSERT INTO token_transactions (user_id, amount, action, description) VALUES ($1,$2,$3,$4)",
                user_id, signup_tokens, "bonus", "Welcome bonus" + referral_note)
            return user_id


async def login_lookup(email: str) -> Optional[dict]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, password_hash, password_salt FROM users WHERE email=$1", email)
        return _record_to_dict(row) if row else None


# ── Users ──

async def create_or_update_user(github_id: int, username: str, email: str = None, avatar_url: str = None) -> int:
    pool = _get_pool()
    now = time.time()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM users WHERE github_id=$1", github_id)
        if row:
            await conn.execute(
                "UPDATE users SET username=$1, email=$2, avatar_url=$3, last_login=$4 WHERE id=$5",
                username, email, avatar_url, now, row["id"])
            return row["id"]
        else:
            new_id = await conn.fetchval(
                "INSERT INTO users (github_id, username, email, avatar_url, tokens) VALUES ($1,$2,$3,$4,10) RETURNING id",
                github_id, username, email, avatar_url)
            await conn.execute(
                "INSERT INTO token_transactions (user_id, amount, action, description) VALUES ($1,$2,$3,$4)",
                new_id, 10, "signup", "Free signup tokens")
            return new_id


async def create_session(user_id: int, ttl_days: int = 30) -> str:
    token = secrets.token_urlsafe(32)
    expires = time.time() + (ttl_days * 86400)
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES ($1,$2,$3)",
            token, user_id, expires)
    return token


async def get_user_by_session(token: str) -> Optional[dict]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id
            WHERE s.token=$1 AND s.expires_at>$2
        """, token, time.time())
        return _record_to_dict(row) if row else None


async def delete_session(token: str):
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM sessions WHERE token=$1", token)


# ── Repos ──

def repo_hash(url: str) -> str:
    normalized = url.rstrip("/").lower().replace(".git", "")
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


async def save_repo(user_id: int, url: str, name: str, tree: str, file_count: int,
                    total_chars: int, languages: dict, repo_text: str, file_index: list) -> int:
    rh = repo_hash(url)
    text_z = zlib.compress(repo_text.encode(), level=6)
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM repos WHERE user_id=$1 AND repo_hash=$2", user_id, rh)
        now = time.time()
        if row:
            await conn.execute("""
                UPDATE repos SET name=$1, tree=$2, file_count=$3, total_chars=$4,
                languages=$5, repo_text_z=$6, file_index=$7, last_accessed=$8 WHERE id=$9
            """, name, tree, file_count, total_chars, json.dumps(languages),
                text_z, json.dumps(file_index), now, row["id"])
            return row["id"]
        else:
            return await conn.fetchval("""
                INSERT INTO repos (user_id, url, name, repo_hash, tree, file_count, total_chars, languages, repo_text_z, file_index)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id
            """, user_id, url, name, rh, tree, file_count, total_chars,
                json.dumps(languages), text_z, json.dumps(file_index))


async def get_user_repos(user_id: int) -> list:
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, url, name, file_count, total_chars, languages, created_at, last_accessed
            FROM repos WHERE user_id=$1 ORDER BY last_accessed DESC
        """, user_id)
        return [_record_to_dict(r) for r in rows]


async def get_repo(repo_id: int, user_id: int) -> Optional[dict]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM repos WHERE id=$1 AND user_id=$2", repo_id, user_id)
        if not row:
            return None
        d = _record_to_dict(row)
        d["languages"] = json.loads(d["languages"]) if d["languages"] else {}
        d["file_index"] = json.loads(d["file_index"]) if d["file_index"] else []
        if d["repo_text_z"]:
            d["repo_text"] = zlib.decompress(bytes(d["repo_text_z"])).decode()
        del d["repo_text_z"]
        await conn.execute("UPDATE repos SET last_accessed=$1 WHERE id=$2", time.time(), repo_id)
        return d


async def delete_repo(repo_id: int, user_id: int):
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM repos WHERE id=$1 AND user_id=$2", repo_id, user_id)


# ── Generated Content ──

async def save_generated(repo_id: int, kind: str, depth: str, expertise: str, content: str):
    pool = _get_pool()
    now = time.time()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO generated (repo_id, kind, depth, expertise, content, created_at)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (repo_id, kind, depth, expertise)
            DO UPDATE SET content=$5, created_at=$6
        """, repo_id, kind, depth, expertise, content, now)


async def get_generated(repo_id: int, kind: str = None) -> list:
    pool = _get_pool()
    async with pool.acquire() as conn:
        if kind:
            rows = await conn.fetch(
                "SELECT * FROM generated WHERE repo_id=$1 AND kind=$2 ORDER BY created_at DESC",
                repo_id, kind)
        else:
            rows = await conn.fetch(
                "SELECT * FROM generated WHERE repo_id=$1 ORDER BY created_at DESC", repo_id)
        return [_record_to_dict(r) for r in rows]


# ── Chats ──

async def save_chat(repo_id: int, role: str, message: str, selection: str = None, file_path: str = None):
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO chats (repo_id, role, message, selection, file_path) VALUES ($1,$2,$3,$4,$5)",
            repo_id, role, message, selection, file_path)


async def get_chats(repo_id: int, limit: int = 50) -> list:
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM chats WHERE repo_id=$1 ORDER BY created_at ASC LIMIT $2",
            repo_id, limit)
        return [_record_to_dict(r) for r in rows]


# ── Tokens ──

async def get_token_balance(user_id: int) -> int:
    pool = _get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval("SELECT tokens FROM users WHERE id=$1", user_id)
        return val or 0


async def spend_tokens(user_id: int, amount: int, description: str) -> bool:
    pool = _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            val = await conn.fetchval("SELECT tokens FROM users WHERE id=$1 FOR UPDATE", user_id)
            if val is None or val < amount:
                return False
            await conn.execute("UPDATE users SET tokens = tokens - $1 WHERE id=$2", amount, user_id)
            await conn.execute(
                "INSERT INTO token_transactions (user_id, amount, action, description) VALUES ($1,$2,$3,$4)",
                user_id, -amount, "spend", description)
            return True


async def add_tokens(user_id: int, amount: int, description: str):
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET tokens = tokens + $1 WHERE id=$2", amount, user_id)
        await conn.execute(
            "INSERT INTO token_transactions (user_id, amount, action, description) VALUES ($1,$2,$3,$4)",
            user_id, amount, "purchase", description)


async def has_ever_purchased(user_id: int) -> bool:
    pool = _get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval("SELECT has_purchased FROM users WHERE id=$1", user_id)
        return bool(val) if val else False


async def set_has_purchased(user_id: int):
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET has_purchased = 1 WHERE id=$1", user_id)


async def get_token_transactions(user_id: int, limit: int = 20) -> list:
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM token_transactions WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2",
            user_id, limit)
        return [_record_to_dict(r) for r in rows]


async def get_user_by_stripe_customer(customer_id: str) -> Optional[dict]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, plan FROM users WHERE stripe_customer_id=$1", customer_id)
        return dict(row) if row else None


# ── Subscriptions ──

async def update_subscription(user_id: int, **kwargs):
    if not kwargs:
        return
    sets = []
    vals = []
    for i, (k, v) in enumerate(kwargs.items(), 1):
        sets.append("{0}=${1}".format(k, i))
        vals.append(v)
    vals.append(user_id)
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET {0} WHERE id=${1}".format(", ".join(sets), len(vals)),
            *vals)


async def get_subscription(user_id: int) -> Optional[dict]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT plan, subscription_status, subscription_id, stripe_customer_id, repos_this_month, month_reset FROM users WHERE id=$1",
            user_id)
        return _record_to_dict(row) if row else None


async def increment_repo_count(user_id: int):
    current_month = datetime.utcnow().strftime("%Y-%m")
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT repos_this_month, month_reset FROM users WHERE id=$1", user_id)
        if row and row["month_reset"] == current_month:
            await conn.execute("UPDATE users SET repos_this_month = repos_this_month + 1 WHERE id=$1", user_id)
        else:
            await conn.execute("UPDATE users SET repos_this_month = 1, month_reset = $1 WHERE id=$2", current_month, user_id)


async def check_repo_limit(user_id: int) -> bool:
    sub = await get_subscription(user_id)
    if not sub:
        return True
    if sub.get("plan") == "pro" and sub.get("subscription_status") == "active":
        return True
    current_month = datetime.utcnow().strftime("%Y-%m")
    if sub.get("month_reset") != current_month:
        return True
    return (sub.get("repos_this_month") or 0) < 3


# ── Jobs ──

async def create_job(job_id: str, kind: str, repo_id: str = None, status: str = "queued", message: str = "Starting..."):
    pool = _get_pool()
    now = time.time()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO job_status (id, kind, status, message, repo_id, created_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (id) DO UPDATE SET kind=$2, status=$3, message=$4, repo_id=$5, updated_at=$7
        """, job_id, kind, status, message, repo_id, now, now)


async def update_job(job_id: str, status: str = None, message: str = None, result: str = None):
    pool = _get_pool()
    now = time.time()
    sets = []
    vals = []
    idx = 1
    if status is not None:
        sets.append("status=${0}".format(idx)); vals.append(status); idx += 1
    if message is not None:
        sets.append("message=${0}".format(idx)); vals.append(message); idx += 1
    if result is not None:
        sets.append("result=${0}".format(idx)); vals.append(result); idx += 1
    sets.append("updated_at=${0}".format(idx)); vals.append(now); idx += 1
    vals.append(job_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE job_status SET {0} WHERE id=${1}".format(", ".join(sets), idx),
            *vals)


async def get_job(job_id: str) -> Optional[dict]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM job_status WHERE id=$1", job_id)
        return _record_to_dict(row) if row else None


async def cleanup_old_jobs(max_age_hours: int = 24):
    cutoff = time.time() - (max_age_hours * 3600)
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM job_status WHERE created_at < $1", cutoff)


# ── Rate Limiting ──

async def check_rate_limit_db(key: str, max_requests: int, window_seconds: int) -> bool:
    now = time.time()
    pool = _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT timestamps FROM rate_limits WHERE key=$1 FOR UPDATE", key)
            timestamps = json.loads(row["timestamps"]) if row else []
            timestamps = [t for t in timestamps if now - t < window_seconds]
            if len(timestamps) >= max_requests:
                return True
            timestamps.append(now)
            await conn.execute("""
                INSERT INTO rate_limits (key, timestamps, updated_at) VALUES ($1,$2,$3)
                ON CONFLICT (key) DO UPDATE SET timestamps=$2, updated_at=$3
            """, key, json.dumps(timestamps), now)
            return False


# ── Anonymous Usage ──

async def check_anonymous_usage(ip: str) -> int:
    pool = _get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval("SELECT overviews_generated FROM anonymous_usage WHERE ip=$1", ip)
        return val or 0


async def increment_anonymous_usage(ip: str):
    pool = _get_pool()
    now = time.time()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO anonymous_usage (ip, overviews_generated, last_used) VALUES ($1, 1, $2)
            ON CONFLICT (ip) DO UPDATE SET overviews_generated = anonymous_usage.overviews_generated + 1, last_used = $2
        """, ip, now)


# ── Public Overviews ──

async def save_public_overview(owner: str, repo_name: str, repo_url: str, overview: str,
                               description: str = None, languages: str = None,
                               stars: int = 0, file_count: int = 0,
                               depth: str = "high-level", expertise: str = "amateur"):
    pool = _get_pool()
    now = time.time()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO public_overviews (owner, repo_name, repo_url, overview, description, languages, stars, file_count, depth, expertise, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (owner, repo_name) DO UPDATE SET
                repo_url=$3, overview=$4, description=$5, languages=$6, stars=$7, file_count=$8, depth=$9, expertise=$10, updated_at=$11
        """, owner, repo_name, repo_url, overview, description, languages, stars, file_count, depth, expertise, now)


async def get_public_overview(owner: str, repo_name: str) -> Optional[dict]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public_overviews WHERE owner=$1 AND repo_name=$2", owner, repo_name)
        return _record_to_dict(row) if row else None


async def list_public_overviews(limit: int = 1000) -> list:
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT owner, repo_name, description, updated_at FROM public_overviews ORDER BY updated_at DESC LIMIT $1",
            limit)
        return [_record_to_dict(r) for r in rows]


async def get_trending_repos(days: int = 7, limit: int = 10) -> list:
    cutoff = time.time() - (days * 86400)
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT url, name, COUNT(*) as cnt FROM repos
            WHERE created_at > $1 GROUP BY url, name ORDER BY cnt DESC LIMIT $2
        """, cutoff, limit)
        return [_record_to_dict(r) for r in rows]


# ── Achievements ──

async def grant_achievement(user_id: int, badge: str) -> bool:
    pool = _get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO user_achievements (user_id, badge) VALUES ($1,$2)",
                user_id, badge)
            return True
        except asyncpg.UniqueViolationError:
            return False


async def get_user_achievements(user_id: int) -> list:
    from db import ACHIEVEMENT_DEFS
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT badge, unlocked_at FROM user_achievements WHERE user_id=$1 ORDER BY unlocked_at DESC",
            user_id)
        result = []
        for r in rows:
            badge = r["badge"]
            defn = ACHIEVEMENT_DEFS.get(badge, {"name": badge, "emoji": "🏆", "desc": ""})
            result.append({
                "badge": badge,
                "name": defn["name"],
                "emoji": defn["emoji"],
                "desc": defn["desc"],
                "unlocked_at": r["unlocked_at"],
            })
        return result


# ── Referrals ──

async def get_referral_code(user_id: int) -> Optional[str]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval("SELECT referral_code FROM users WHERE id=$1", user_id)
        if val:
            return val
    return await generate_referral_code(user_id)


async def generate_referral_code(user_id: int) -> str:
    code = secrets.token_urlsafe(8)
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET referral_code=$1 WHERE id=$2", code, user_id)
    return code


async def get_user_by_referral(code: str) -> Optional[dict]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, username FROM users WHERE referral_code=$1", code)
        return _record_to_dict(row) if row else None


async def set_referred_by(user_id: int, referrer_id: int):
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET referred_by=$1 WHERE id=$2", referrer_id, user_id)


# ── API Keys ──

async def generate_api_key(user_id: int) -> str:
    key = "rplm_" + secrets.token_urlsafe(32)
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET api_key=$1 WHERE id=$2", key, user_id)
    return key


async def get_user_by_api_key(api_key: str) -> Optional[dict]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE api_key=$1", api_key)
        return _record_to_dict(row) if row else None


async def track_api_usage(user_id: int, api_key: str, endpoint: str, tokens_used: int = 0):
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_usage (user_id, api_key, endpoint, tokens_used) VALUES ($1,$2,$3,$4)",
            user_id, api_key, endpoint, tokens_used)


async def check_api_rate_limit(user_id: int, daily_limit: int) -> bool:
    today = time.strftime("%Y-%m-%d")
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT api_calls_today, api_calls_date FROM users WHERE id=$1", user_id)
        if not row:
            return False
        if row["api_calls_date"] != today:
            await conn.execute("UPDATE users SET api_calls_today=1, api_calls_date=$1 WHERE id=$2", today, user_id)
            return True
        if (row["api_calls_today"] or 0) >= daily_limit:
            return False
        await conn.execute("UPDATE users SET api_calls_today = api_calls_today + 1 WHERE id=$1", user_id)
        return True


async def get_api_usage_stats(user_id: int, days: int = 30) -> dict:
    cutoff = time.time() - (days * 86400)
    pool = _get_pool()
    async with pool.acquire() as conn:
        total_calls = await conn.fetchval(
            "SELECT COUNT(*) FROM api_usage WHERE user_id=$1 AND created_at>$2", user_id, cutoff)
        total_tokens = await conn.fetchval(
            "SELECT COALESCE(SUM(tokens_used),0) FROM api_usage WHERE user_id=$1 AND created_at>$2", user_id, cutoff)
        by_endpoint = await conn.fetch(
            "SELECT endpoint, COUNT(*) as cnt FROM api_usage WHERE user_id=$1 AND created_at>$2 GROUP BY endpoint ORDER BY cnt DESC",
            user_id, cutoff)
    return {
        "total_calls": total_calls,
        "total_tokens": total_tokens,
        "by_endpoint": [{"endpoint": r["endpoint"], "calls": r["cnt"]} for r in by_endpoint],
    }


# ── Email Preferences ──

async def get_email_preferences(user_id: int) -> dict:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM email_preferences WHERE user_id=$1", user_id)
        if row:
            return _record_to_dict(row)
        return {"user_id": user_id, "welcome": 1, "generation_ready": 1, "weekly_digest": 1, "marketing": 1}


async def update_email_preferences(user_id: int, **kwargs):
    pool = _get_pool()
    now = time.time()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO email_preferences (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING
        """, user_id)
        for key, val in kwargs.items():
            if key in ("welcome", "generation_ready", "weekly_digest", "marketing"):
                await conn.execute(
                    "UPDATE email_preferences SET {0}=$1, updated_at=$2 WHERE user_id=$3".format(key),
                    int(val), now, user_id)


# ── Share Tracking ──

async def increment_share_count(content_id: str, platform: str = "link"):
    pool = _get_pool()
    now = time.time()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO share_counts (content_id, platform, count, last_shared) VALUES ($1,$2,1,$3)
            ON CONFLICT (content_id) DO UPDATE SET count = share_counts.count + 1, last_shared = $3
        """, content_id, platform, now)


async def get_share_count(content_id: str) -> int:
    pool = _get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval("SELECT count FROM share_counts WHERE content_id=$1", content_id)
        return val or 0


# ── Admin Stats ──

async def get_db_stats() -> dict:
    pool = _get_pool()
    async with pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        repos = await conn.fetchval("SELECT COUNT(*) FROM repos")
        total_z = await conn.fetchval("SELECT COALESCE(SUM(LENGTH(repo_text_z)),0) FROM repos")
        generated = await conn.fetchval("SELECT COUNT(*) FROM generated")
        chats = await conn.fetchval("SELECT COUNT(*) FROM chats")
    return {
        "users": users, "repos": repos, "generated": generated, "chats": chats,
        "compressed_repo_bytes": total_z, "db_size_bytes": 0,
    }


async def get_admin_stats() -> dict:
    now = time.time()
    pool = _get_pool()
    async with pool.acquire() as conn:
        active_24h = await conn.fetchval(
            "SELECT COUNT(DISTINCT user_id) FROM sessions WHERE created_at > $1", now - 86400)
        signups_today = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at > $1", now - 86400)
        signups_week = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at > $1", now - 7 * 86400)
        signups_month = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at > $1", now - 30 * 86400)
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_revenue_tokens = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM token_transactions WHERE action='purchase'")
        paid_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE has_purchased = 1")
        active_subs = await conn.fetchval("SELECT COUNT(*) FROM users WHERE subscription_status = 'active'")
        top_repos = await conn.fetch("""
            SELECT name, url, COUNT(*) as cnt FROM repos GROUP BY url, name ORDER BY cnt DESC LIMIT 10
        """)
        gen_by_type = await conn.fetch("""
            SELECT kind, COUNT(*) as cnt FROM generated GROUP BY kind ORDER BY cnt DESC
        """)
        total_generated = await conn.fetchval("SELECT COUNT(*) FROM generated")
        total_chats = await conn.fetchval("SELECT COUNT(*) FROM chats")
        public_pages = await conn.fetchval("SELECT COUNT(*) FROM public_overviews")

    return {
        "active_users_24h": active_24h,
        "signups": {"today": signups_today, "week": signups_week, "month": signups_month},
        "total_users": total_users,
        "paid_users": paid_users,
        "active_subscriptions": active_subs,
        "total_tokens_purchased": total_revenue_tokens,
        "total_generated": total_generated,
        "total_chats": total_chats,
        "public_pages": public_pages,
        "generation_by_type": [{"kind": r["kind"], "count": r["cnt"]} for r in gen_by_type],
        "top_repos": [{"name": r["name"], "url": r["url"], "count": r["cnt"]} for r in top_repos],
    }
