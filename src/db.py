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

DB_PATH = os.path.join(os.path.dirname(__file__), "repolm.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


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
        # Migration: add subscription columns to users if missing
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        migrations = {
            "stripe_customer_id": "ALTER TABLE users ADD COLUMN stripe_customer_id TEXT",
            "subscription_status": "ALTER TABLE users ADD COLUMN subscription_status TEXT DEFAULT 'none'",
            "subscription_id": "ALTER TABLE users ADD COLUMN subscription_id TEXT",
            "plan": "ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'",
            "repos_this_month": "ALTER TABLE users ADD COLUMN repos_this_month INTEGER DEFAULT 0",
            "month_reset": "ALTER TABLE users ADD COLUMN month_reset TEXT",
        }
        for col, sql in migrations.items():
            if col not in cols:
                conn.execute(sql)


# ── Users ──

def create_or_update_user(github_id: int, username: str, email: str = None, avatar_url: str = None) -> int:
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE github_id=?", (github_id,)).fetchone()
        if row:
            conn.execute("UPDATE users SET username=?, email=?, avatar_url=?, last_login=unixepoch() WHERE id=?",
                         (username, email, avatar_url, row["id"]))
            return row["id"]
        else:
            cur = conn.execute("INSERT INTO users (github_id, username, email, avatar_url) VALUES (?,?,?,?)",
                               (github_id, username, email, avatar_url))
            return cur.lastrowid


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


# Init on import
init_db()
