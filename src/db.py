"""
RepoLM — Database layer (SQLite)
Efficient storage: repo metadata + generated content + chat in DB.
Raw repo text stored compressed. File contents NOT stored — re-cloned on demand.
"""

import os
import sqlite3
import json
import zlib
import time
import hashlib
import secrets
from contextlib import contextmanager
from typing import Optional, List, Dict
from datetime import datetime

import logging

logger = logging.getLogger("repolm")

_DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
DB_PATH = os.path.join(_DATA_DIR, "repolm.db")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def db():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            conn.rollback()
            raise
        raise
    finally:
        conn.close()


def db_retry(fn, retries=3):
    """Execute fn() with retry on database locked errors."""
    for attempt in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < retries - 1:
                wait = 0.1 * (2 ** attempt)
                logger.warning("DB locked, retry %d/%d in %.1fs", attempt + 1, retries, wait)
                time.sleep(wait)
            else:
                raise


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            github_id INTEGER UNIQUE,
            username TEXT NOT NULL,
            email TEXT,
            avatar_url TEXT,
            created_at REAL DEFAULT (unixepoch()),
            last_login REAL DEFAULT (unixepoch())
        );

        CREATE TABLE IF NOT EXISTS course_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            description TEXT,
            repo_url TEXT,
            price_cents INTEGER DEFAULT 2900,
            created_at REAL DEFAULT (unixepoch())
        );

        CREATE TABLE IF NOT EXISTS purchased_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            pack_id INTEGER NOT NULL REFERENCES course_packs(id),
            stripe_payment_id TEXT,
            purchased_at REAL DEFAULT (unixepoch()),
            UNIQUE(user_id, pack_id)
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at REAL DEFAULT (unixepoch()),
            expires_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

        CREATE TABLE IF NOT EXISTS repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            url TEXT NOT NULL,
            name TEXT NOT NULL,
            repo_hash TEXT NOT NULL,
            tree TEXT,
            file_count INTEGER,
            total_chars INTEGER,
            languages TEXT,  -- JSON
            repo_text_z BLOB,  -- zlib compressed full text (for LLM context)
            file_index TEXT,  -- JSON array of {path, size, is_priority} (no content)
            created_at REAL DEFAULT (unixepoch()),
            last_accessed REAL DEFAULT (unixepoch()),
            UNIQUE(user_id, repo_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_repos_user ON repos(user_id);

        CREATE TABLE IF NOT EXISTS generated (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,  -- overview, podcast, slides
            depth TEXT NOT NULL,
            expertise TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL DEFAULT (unixepoch())
        );
        CREATE INDEX IF NOT EXISTS idx_generated_repo ON generated(repo_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_generated_unique ON generated(repo_id, kind, depth, expertise);

        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
            role TEXT NOT NULL,  -- user, assistant
            message TEXT NOT NULL,
            selection TEXT,  -- highlighted text if immersive
            file_path TEXT,  -- file context if immersive
            created_at REAL DEFAULT (unixepoch())
        );
        CREATE INDEX IF NOT EXISTS idx_chats_repo ON chats(repo_id);
        """)
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS token_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            amount INTEGER NOT NULL,
            action TEXT NOT NULL,
            description TEXT,
            created_at REAL DEFAULT (unixepoch())
        );
        CREATE INDEX IF NOT EXISTS idx_token_tx_user ON token_transactions(user_id);
        """)
        # Migration: add columns to users if missing
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        migrations = {
            "stripe_customer_id": "ALTER TABLE users ADD COLUMN stripe_customer_id TEXT",
            "subscription_status": "ALTER TABLE users ADD COLUMN subscription_status TEXT DEFAULT 'none'",
            "subscription_id": "ALTER TABLE users ADD COLUMN subscription_id TEXT",
            "plan": "ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'",
            "repos_this_month": "ALTER TABLE users ADD COLUMN repos_this_month INTEGER DEFAULT 0",
            "month_reset": "ALTER TABLE users ADD COLUMN month_reset TEXT",
            "tokens": "ALTER TABLE users ADD COLUMN tokens INTEGER DEFAULT 0",
            "has_purchased": "ALTER TABLE users ADD COLUMN has_purchased INTEGER DEFAULT 0",
            "password_hash": "ALTER TABLE users ADD COLUMN password_hash TEXT",
            "password_salt": "ALTER TABLE users ADD COLUMN password_salt TEXT",
        }
        for col, sql in migrations.items():
            if col not in cols:
                conn.execute(sql)

        # Referral and API key migrations
        extra_migrations = {
            "referral_code": "ALTER TABLE users ADD COLUMN referral_code TEXT",
            "referred_by": "ALTER TABLE users ADD COLUMN referred_by INTEGER",
            "api_key": "ALTER TABLE users ADD COLUMN api_key TEXT",
            "api_calls_today": "ALTER TABLE users ADD COLUMN api_calls_today INTEGER DEFAULT 0",
            "api_calls_date": "ALTER TABLE users ADD COLUMN api_calls_date TEXT",
        }
        for col, sql in extra_migrations.items():
            if col not in cols:
                conn.execute(sql)

        # Job status table (multi-worker safe)
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS job_status (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            message TEXT DEFAULT '',
            result TEXT,
            repo_id TEXT,
            created_at REAL DEFAULT (unixepoch()),
            updated_at REAL DEFAULT (unixepoch())
        );
        CREATE INDEX IF NOT EXISTS idx_job_status_repo ON job_status(repo_id);

        CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT PRIMARY KEY,
            timestamps TEXT NOT NULL DEFAULT '[]',
            updated_at REAL DEFAULT (unixepoch())
        );
        """)

        # API usage tracking table
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            api_key TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            tokens_used INTEGER DEFAULT 0,
            created_at REAL DEFAULT (unixepoch())
        );
        CREATE INDEX IF NOT EXISTS idx_api_usage_user ON api_usage(user_id);
        CREATE INDEX IF NOT EXISTS idx_api_usage_key ON api_usage(api_key);
        """)

        # Public overviews for SEO pages
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS public_overviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            created_at REAL DEFAULT (unixepoch()),
            updated_at REAL DEFAULT (unixepoch()),
            UNIQUE(owner, repo_name)
        );
        CREATE INDEX IF NOT EXISTS idx_public_owner_repo ON public_overviews(owner, repo_name);
        """)

        # User achievements / gamification
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            badge TEXT NOT NULL,
            unlocked_at REAL DEFAULT (unixepoch()),
            UNIQUE(user_id, badge)
        );
        CREATE INDEX IF NOT EXISTS idx_achievements_user ON user_achievements(user_id);
        """)

        # Anonymous usage tracking (first-free-repo)
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS anonymous_usage (
            ip TEXT PRIMARY KEY,
            overviews_generated INTEGER DEFAULT 0,
            last_used REAL DEFAULT (unixepoch())
        );
        """)

        # Email preferences
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS email_preferences (
            user_id INTEGER PRIMARY KEY REFERENCES users(id),
            welcome INTEGER DEFAULT 1,
            generation_ready INTEGER DEFAULT 1,
            weekly_digest INTEGER DEFAULT 1,
            marketing INTEGER DEFAULT 1,
            updated_at REAL DEFAULT (unixepoch())
        );
        """)

        # Share tracking
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS share_counts (
            content_id TEXT PRIMARY KEY,
            platform TEXT,
            count INTEGER DEFAULT 0,
            last_shared REAL DEFAULT (unixepoch())
        );
        """)


# ── Users ──

def create_or_update_user(github_id: int, username: str, email: str = None, avatar_url: str = None) -> int:
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE github_id=?", (github_id,)).fetchone()
        if row:
            conn.execute("UPDATE users SET username=?, email=?, avatar_url=?, last_login=unixepoch() WHERE id=?",
                         (username, email, avatar_url, row["id"]))
            return row["id"]
        else:
            cur = conn.execute("INSERT INTO users (github_id, username, email, avatar_url, tokens) VALUES (?,?,?,?,10)",
                               (github_id, username, email, avatar_url))
            user_id = cur.lastrowid
            conn.execute("INSERT INTO token_transactions (user_id, amount, action, description) VALUES (?,?,?,?)",
                         (user_id, 10, "signup", "Free signup tokens"))
            return user_id


def create_session(user_id: int, ttl_days: int = 30) -> str:
    token = secrets.token_urlsafe(32)
    expires = time.time() + (ttl_days * 86400)
    with db() as conn:
        conn.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
                     (token, user_id, expires))
    return token


def get_user_by_session(token: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute("""
            SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id
            WHERE s.token=? AND s.expires_at>?
        """, (token, time.time())).fetchone()
        return dict(row) if row else None


def delete_session(token: str):
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


# ── Repos ──

def repo_hash(url: str) -> str:
    """Consistent hash for a repo URL."""
    normalized = url.rstrip("/").lower().replace(".git", "")
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def save_repo(user_id: int, url: str, name: str, tree: str, file_count: int,
              total_chars: int, languages: dict, repo_text: str, file_index: list) -> int:
    rh = repo_hash(url)
    text_z = zlib.compress(repo_text.encode(), level=6)
    with db() as conn:
        # Upsert
        row = conn.execute("SELECT id FROM repos WHERE user_id=? AND repo_hash=?", (user_id, rh)).fetchone()
        if row:
            conn.execute("""UPDATE repos SET name=?, tree=?, file_count=?, total_chars=?,
                           languages=?, repo_text_z=?, file_index=?, last_accessed=unixepoch()
                           WHERE id=?""",
                         (name, tree, file_count, total_chars, json.dumps(languages),
                          text_z, json.dumps(file_index), row["id"]))
            return row["id"]
        else:
            cur = conn.execute("""INSERT INTO repos (user_id, url, name, repo_hash, tree, file_count,
                                  total_chars, languages, repo_text_z, file_index)
                                  VALUES (?,?,?,?,?,?,?,?,?,?)""",
                               (user_id, url, name, rh, tree, file_count, total_chars,
                                json.dumps(languages), text_z, json.dumps(file_index)))
            return cur.lastrowid


def get_user_repos(user_id: int) -> list:
    with db() as conn:
        rows = conn.execute("""SELECT id, url, name, file_count, total_chars, languages, created_at, last_accessed
                              FROM repos WHERE user_id=? ORDER BY last_accessed DESC""", (user_id,)).fetchall()
        return [dict(r) for r in rows]


def get_repo(repo_id: int, user_id: int) -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM repos WHERE id=? AND user_id=?", (repo_id, user_id)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["languages"] = json.loads(d["languages"]) if d["languages"] else {}
        d["file_index"] = json.loads(d["file_index"]) if d["file_index"] else []
        if d["repo_text_z"]:
            d["repo_text"] = zlib.decompress(d["repo_text_z"]).decode()
        del d["repo_text_z"]
        conn.execute("UPDATE repos SET last_accessed=unixepoch() WHERE id=?", (repo_id,))
        return d


def delete_repo(repo_id: int, user_id: int):
    with db() as conn:
        conn.execute("DELETE FROM repos WHERE id=? AND user_id=?", (repo_id, user_id))


# ── Generated content ──

def save_generated(repo_id: int, kind: str, depth: str, expertise: str, content: str):
    with db() as conn:
        conn.execute("""INSERT OR REPLACE INTO generated (repo_id, kind, depth, expertise, content, created_at)
                       VALUES (?,?,?,?,?,unixepoch())""",
                     (repo_id, kind, depth, expertise, content))


def get_generated(repo_id: int, kind: str = None) -> list:
    with db() as conn:
        if kind:
            rows = conn.execute("SELECT * FROM generated WHERE repo_id=? AND kind=? ORDER BY created_at DESC",
                                (repo_id, kind)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM generated WHERE repo_id=? ORDER BY created_at DESC",
                                (repo_id,)).fetchall()
        return [dict(r) for r in rows]


# ── Chats ──

def save_chat(repo_id: int, role: str, message: str, selection: str = None, file_path: str = None):
    with db() as conn:
        conn.execute("INSERT INTO chats (repo_id, role, message, selection, file_path) VALUES (?,?,?,?,?)",
                     (repo_id, role, message, selection, file_path))


def get_chats(repo_id: int, limit: int = 50) -> list:
    with db() as conn:
        rows = conn.execute("SELECT * FROM chats WHERE repo_id=? ORDER BY created_at ASC LIMIT ?",
                            (repo_id, limit)).fetchall()
        return [dict(r) for r in rows]


# ── Tokens ──

def get_token_balance(user_id: int) -> int:
    with db() as conn:
        row = conn.execute("SELECT tokens FROM users WHERE id=?", (user_id,)).fetchone()
        return row["tokens"] if row else 0


def spend_tokens(user_id: int, amount: int, description: str) -> bool:
    """Deduct tokens. Returns True if successful, False if insufficient."""
    with db() as conn:
        row = conn.execute("SELECT tokens FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or row["tokens"] < amount:
            return False
        conn.execute("UPDATE users SET tokens = tokens - ? WHERE id=?", (amount, user_id))
        conn.execute("INSERT INTO token_transactions (user_id, amount, action, description) VALUES (?,?,?,?)",
                     (user_id, -amount, "spend", description))
        return True


def add_tokens(user_id: int, amount: int, description: str):
    with db() as conn:
        conn.execute("UPDATE users SET tokens = tokens + ? WHERE id=?", (amount, user_id))
        conn.execute("INSERT INTO token_transactions (user_id, amount, action, description) VALUES (?,?,?,?)",
                     (user_id, amount, "purchase", description))


def has_ever_purchased(user_id: int) -> bool:
    with db() as conn:
        row = conn.execute("SELECT has_purchased FROM users WHERE id=?", (user_id,)).fetchone()
        return bool(row["has_purchased"]) if row else False


def set_has_purchased(user_id: int):
    with db() as conn:
        conn.execute("UPDATE users SET has_purchased = 1 WHERE id=?", (user_id,))


def get_token_transactions(user_id: int, limit: int = 20) -> list:
    with db() as conn:
        rows = conn.execute("SELECT * FROM token_transactions WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                            (user_id, limit)).fetchall()
        return [dict(r) for r in rows]


# ── Subscriptions ──

def update_subscription(user_id: int, **kwargs):
    """Update subscription fields for a user. Pass any of: stripe_customer_id, subscription_id, subscription_status, plan."""
    if not kwargs:
        return
    sets = []
    vals = []
    for k, v in kwargs.items():
        sets.append(f"{k}=?")
        vals.append(v)
    vals.append(user_id)
    with db() as conn:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", vals)


def get_subscription(user_id: int) -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT plan, subscription_status, subscription_id, stripe_customer_id, repos_this_month, month_reset FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def increment_repo_count(user_id: int):
    current_month = datetime.utcnow().strftime("%Y-%m")
    with db() as conn:
        row = conn.execute("SELECT repos_this_month, month_reset FROM users WHERE id=?", (user_id,)).fetchone()
        if row and row["month_reset"] == current_month:
            conn.execute("UPDATE users SET repos_this_month = repos_this_month + 1 WHERE id=?", (user_id,))
        else:
            conn.execute("UPDATE users SET repos_this_month = 1, month_reset = ? WHERE id=?", (current_month, user_id))


def check_repo_limit(user_id: int) -> bool:
    """Returns True if user can add a repo, False if at limit."""
    sub = get_subscription(user_id)
    if not sub:
        return True  # no user record, allow
    if sub.get("plan") == "pro" and sub.get("subscription_status") == "active":
        return True  # pro users unlimited
    current_month = datetime.utcnow().strftime("%Y-%m")
    if sub.get("month_reset") != current_month:
        return True  # new month, reset
    return (sub.get("repos_this_month") or 0) < 3


def get_db_stats() -> dict:
    """Storage stats."""
    with db() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        repos = conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
        total_z = conn.execute("SELECT COALESCE(SUM(LENGTH(repo_text_z)),0) FROM repos").fetchone()[0]
        generated = conn.execute("SELECT COUNT(*) FROM generated").fetchone()[0]
        chats = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
    db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    return {
        "users": users, "repos": repos, "generated": generated, "chats": chats,
        "compressed_repo_bytes": total_z, "db_size_bytes": db_size,
    }


# ── Referrals ──

def generate_referral_code(user_id: int) -> str:
    """Generate and store a referral code for a user."""
    code = secrets.token_urlsafe(8)
    with db() as conn:
        conn.execute("UPDATE users SET referral_code=? WHERE id=?", (code, user_id))
    return code


def get_referral_code(user_id: int) -> Optional[str]:
    """Get or create a referral code for a user."""
    with db() as conn:
        row = conn.execute("SELECT referral_code FROM users WHERE id=?", (user_id,)).fetchone()
        if row and row["referral_code"]:
            return row["referral_code"]
    return generate_referral_code(user_id)


def get_user_by_referral(code: str) -> Optional[dict]:
    """Find user by referral code."""
    with db() as conn:
        row = conn.execute("SELECT id, username FROM users WHERE referral_code=?", (code,)).fetchone()
        return dict(row) if row else None


def set_referred_by(user_id: int, referrer_id: int):
    """Mark that a user was referred by another user."""
    with db() as conn:
        conn.execute("UPDATE users SET referred_by=? WHERE id=?", (referrer_id, user_id))


# ── API Keys ──

def generate_api_key(user_id: int) -> str:
    """Generate and store an API key for a user."""
    key = "rplm_" + secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute("UPDATE users SET api_key=? WHERE id=?", (key, user_id))
    return key


def get_user_by_api_key(api_key: str) -> Optional[dict]:
    """Find user by API key."""
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE api_key=?", (api_key,)).fetchone()
        return dict(row) if row else None


def track_api_usage(user_id: int, api_key: str, endpoint: str, tokens_used: int = 0):
    """Log an API call."""
    with db() as conn:
        conn.execute(
            "INSERT INTO api_usage (user_id, api_key, endpoint, tokens_used) VALUES (?,?,?,?)",
            (user_id, api_key, endpoint, tokens_used)
        )


def check_api_rate_limit(user_id: int, daily_limit: int) -> bool:
    """Check if user is within API rate limit. Returns True if allowed."""
    today = time.strftime("%Y-%m-%d")
    with db() as conn:
        row = conn.execute("SELECT api_calls_today, api_calls_date FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return False
        if row["api_calls_date"] != today:
            conn.execute("UPDATE users SET api_calls_today=1, api_calls_date=? WHERE id=?", (today, user_id))
            return True
        if (row["api_calls_today"] or 0) >= daily_limit:
            return False
        conn.execute("UPDATE users SET api_calls_today = api_calls_today + 1 WHERE id=?", (user_id,))
        return True


def get_api_usage_stats(user_id: int, days: int = 30) -> dict:
    """Get API usage statistics for a user."""
    cutoff = time.time() - (days * 86400)
    with db() as conn:
        total_calls = conn.execute(
            "SELECT COUNT(*) FROM api_usage WHERE user_id=? AND created_at>?", (user_id, cutoff)
        ).fetchone()[0]
        total_tokens = conn.execute(
            "SELECT COALESCE(SUM(tokens_used),0) FROM api_usage WHERE user_id=? AND created_at>?", (user_id, cutoff)
        ).fetchone()[0]
        by_endpoint = conn.execute(
            "SELECT endpoint, COUNT(*) as cnt FROM api_usage WHERE user_id=? AND created_at>? GROUP BY endpoint ORDER BY cnt DESC",
            (user_id, cutoff)
        ).fetchall()
    return {
        "total_calls": total_calls,
        "total_tokens": total_tokens,
        "by_endpoint": [{"endpoint": r["endpoint"], "calls": r["cnt"]} for r in by_endpoint],
    }


# ── Job Status (multi-worker safe) ──

def create_job(job_id: str, kind: str, repo_id: str = None, status: str = "queued", message: str = "Starting..."):
    def _do():
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO job_status (id, kind, status, message, repo_id, created_at, updated_at) VALUES (?,?,?,?,?,unixepoch(),unixepoch())",
                (job_id, kind, status, message, repo_id))
    db_retry(_do)


def update_job(job_id: str, status: str = None, message: str = None, result: str = None):
    def _do():
        with db() as conn:
            sets, vals = [], []
            if status is not None:
                sets.append("status=?"); vals.append(status)
            if message is not None:
                sets.append("message=?"); vals.append(message)
            if result is not None:
                sets.append("result=?"); vals.append(result)
            sets.append("updated_at=unixepoch()")
            vals.append(job_id)
            conn.execute(f"UPDATE job_status SET {', '.join(sets)} WHERE id=?", vals)
    db_retry(_do)


def get_job(job_id: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM job_status WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def cleanup_old_jobs(max_age_hours: int = 24):
    cutoff = time.time() - (max_age_hours * 3600)
    with db() as conn:
        conn.execute("DELETE FROM job_status WHERE created_at < ?", (cutoff,))


# ── Rate Limiting (multi-worker safe) ──

def check_rate_limit_db(key: str, max_requests: int, window_seconds: int) -> bool:
    """Returns True if rate limited."""
    now = time.time()
    def _do():
        with db() as conn:
            row = conn.execute("SELECT timestamps FROM rate_limits WHERE key=?", (key,)).fetchone()
            if row:
                timestamps = json.loads(row["timestamps"])
            else:
                timestamps = []
            timestamps = [t for t in timestamps if now - t < window_seconds]
            if len(timestamps) >= max_requests:
                return True
            timestamps.append(now)
            conn.execute(
                "INSERT OR REPLACE INTO rate_limits (key, timestamps, updated_at) VALUES (?,?,?)",
                (key, json.dumps(timestamps), now))
            return False
    return db_retry(_do)


def cleanup_rate_limits():
    """Remove stale rate limit entries."""
    cutoff = time.time() - 7200
    with db() as conn:
        conn.execute("DELETE FROM rate_limits WHERE updated_at < ?", (cutoff,))


# ── Public Overviews (SEO) ──

def save_public_overview(owner: str, repo_name: str, repo_url: str, overview: str,
                         description: str = None, languages: str = None,
                         stars: int = 0, file_count: int = 0,
                         depth: str = "high-level", expertise: str = "amateur"):
    with db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO public_overviews
            (owner, repo_name, repo_url, overview, description, languages, stars, file_count, depth, expertise, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,unixepoch())
        """, (owner, repo_name, repo_url, overview, description, languages, stars, file_count, depth, expertise))


def get_public_overview(owner: str, repo_name: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM public_overviews WHERE owner=? AND repo_name=?",
            (owner, repo_name)
        ).fetchone()
        return dict(row) if row else None


def list_public_overviews(limit: int = 1000) -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT owner, repo_name, description, updated_at FROM public_overviews ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_trending_repos(days: int = 7, limit: int = 10) -> list:
    """Get most-analyzed repos in the last N days from analytics."""
    cutoff = time.time() - (days * 86400)
    with db() as conn:
        rows = conn.execute("""
            SELECT url, name, COUNT(*) as cnt FROM repos
            WHERE created_at > ? GROUP BY url ORDER BY cnt DESC LIMIT ?
        """, (cutoff, limit)).fetchall()
        return [dict(r) for r in rows]


# ── Achievements ──

ACHIEVEMENT_DEFS = {
    "first_overview": {"name": "First Overview", "emoji": "📖", "desc": "Generated your first overview"},
    "podcast_pioneer": {"name": "Podcast Pioneer", "emoji": "🎙️", "desc": "Generated your first podcast"},
    "slide_master": {"name": "Slide Master", "emoji": "📊", "desc": "Generated your first slide deck"},
    "ten_repos": {"name": "10 Repos Analyzed", "emoji": "🔟", "desc": "Analyzed 10 repositories"},
    "concept_explorer": {"name": "Concept Lab Explorer", "emoji": "🧪", "desc": "Used the Concept Lab"},
    "chatterbox": {"name": "Chatterbox", "emoji": "💬", "desc": "Sent 50 chat messages"},
    "audio_listener": {"name": "Audio Listener", "emoji": "🎧", "desc": "Generated your first podcast audio"},
    "sharer": {"name": "Sharer", "emoji": "🔗", "desc": "Shared your first content"},
    "streak_3": {"name": "3-Day Streak", "emoji": "🔥", "desc": "Analyzed repos 3 days in a row"},
    "big_spender": {"name": "Big Spender", "emoji": "💰", "desc": "Purchased tokens for the first time"},
}


def grant_achievement(user_id: int, badge: str) -> bool:
    """Grant an achievement. Returns True if newly granted."""
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO user_achievements (user_id, badge) VALUES (?,?)",
                (user_id, badge)
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_user_achievements(user_id: int) -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT badge, unlocked_at FROM user_achievements WHERE user_id=? ORDER BY unlocked_at DESC",
            (user_id,)
        ).fetchall()
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


# ── Anonymous Usage (first-free) ──

def check_anonymous_usage(ip: str) -> int:
    """Returns number of overviews generated by this IP."""
    with db() as conn:
        row = conn.execute("SELECT overviews_generated FROM anonymous_usage WHERE ip=?", (ip,)).fetchone()
        return row["overviews_generated"] if row else 0


def increment_anonymous_usage(ip: str):
    with db() as conn:
        conn.execute("""
            INSERT INTO anonymous_usage (ip, overviews_generated, last_used)
            VALUES (?, 1, unixepoch())
            ON CONFLICT(ip) DO UPDATE SET overviews_generated = overviews_generated + 1, last_used = unixepoch()
        """, (ip,))


# ── Email Preferences ──

def get_email_preferences(user_id: int) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM email_preferences WHERE user_id=?", (user_id,)).fetchone()
        if row:
            return dict(row)
        return {"user_id": user_id, "welcome": 1, "generation_ready": 1, "weekly_digest": 1, "marketing": 1}


def update_email_preferences(user_id: int, **kwargs):
    with db() as conn:
        conn.execute("""
            INSERT INTO email_preferences (user_id) VALUES (?)
            ON CONFLICT(user_id) DO NOTHING
        """, (user_id,))
        for key, val in kwargs.items():
            if key in ("welcome", "generation_ready", "weekly_digest", "marketing"):
                conn.execute(f"UPDATE email_preferences SET {key}=?, updated_at=unixepoch() WHERE user_id=?",
                             (int(val), user_id))


# ── Share Tracking ──

def increment_share_count(content_id: str, platform: str = "link"):
    with db() as conn:
        conn.execute("""
            INSERT INTO share_counts (content_id, platform, count, last_shared)
            VALUES (?, ?, 1, unixepoch())
            ON CONFLICT(content_id) DO UPDATE SET count = count + 1, last_shared = unixepoch()
        """, (content_id, platform))


def get_share_count(content_id: str) -> int:
    with db() as conn:
        row = conn.execute("SELECT count FROM share_counts WHERE content_id=?", (content_id,)).fetchone()
        return row["count"] if row else 0


# ── Admin Stats ──

def get_admin_stats() -> dict:
    """Comprehensive admin stats."""
    now = time.time()
    with db() as conn:
        active_24h = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM sessions WHERE created_at > ?", (now - 86400,)
        ).fetchone()[0]
        signups_today = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at > ?", (now - 86400,)
        ).fetchone()[0]
        signups_week = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at > ?", (now - 7 * 86400,)
        ).fetchone()[0]
        signups_month = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at > ?", (now - 30 * 86400,)
        ).fetchone()[0]
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_revenue_tokens = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM token_transactions WHERE action='purchase'"
        ).fetchone()[0]
        paid_users = conn.execute(
            "SELECT COUNT(*) FROM users WHERE has_purchased = 1"
        ).fetchone()[0]
        active_subs = conn.execute(
            "SELECT COUNT(*) FROM users WHERE subscription_status = 'active'"
        ).fetchone()[0]
        top_repos = conn.execute("""
            SELECT name, url, COUNT(*) as cnt FROM repos
            GROUP BY url ORDER BY cnt DESC LIMIT 10
        """).fetchall()
        gen_by_type = conn.execute("""
            SELECT kind, COUNT(*) as cnt FROM generated
            GROUP BY kind ORDER BY cnt DESC
        """).fetchall()
        total_generated = conn.execute("SELECT COUNT(*) FROM generated").fetchone()[0]
        total_chats = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        public_pages = conn.execute("SELECT COUNT(*) FROM public_overviews").fetchone()[0]

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


# Init on import
init_db()
