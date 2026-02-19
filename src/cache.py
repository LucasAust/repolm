"""
RepoLM — SQLite-backed cache for generated content.
Avoids re-generating popular repos. 7-day TTL.
"""

import os
import sqlite3
import hashlib
import time
import json
from contextlib import contextmanager

_DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
CACHE_DB_PATH = os.path.join(_DATA_DIR, "repolm_cache.db")
DEFAULT_TTL = 7 * 24 * 3600  # 7 days


def _get_db():
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def _db():
    conn = _get_db()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_cache():
    with _db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS cache_entries (
            cache_key TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            repo_url TEXT,
            kind TEXT,
            created_at REAL DEFAULT (unixepoch()),
            expires_at REAL NOT NULL,
            hit_count INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache_entries(expires_at);
        CREATE INDEX IF NOT EXISTS idx_cache_repo ON cache_entries(repo_url);
        """)


def make_cache_key(repo_url: str, kind: str, depth: str, expertise: str) -> str:
    """Create a deterministic cache key from generation parameters."""
    raw = f"{repo_url.strip().lower().rstrip('/').replace('.git', '')}|{kind}|{depth}|{expertise}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_cached(repo_url: str, kind: str, depth: str, expertise: str) -> str:
    """Return cached content or None if miss/expired."""
    key = make_cache_key(repo_url, kind, depth, expertise)
    with _db() as conn:
        row = conn.execute(
            "SELECT content FROM cache_entries WHERE cache_key=? AND expires_at > ?",
            (key, time.time())
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE cache_entries SET hit_count = hit_count + 1 WHERE cache_key=?",
                (key,)
            )
            return row["content"]
    return None


def set_cached(repo_url: str, kind: str, depth: str, expertise: str, content: str, ttl: int = DEFAULT_TTL):
    """Store content in cache."""
    key = make_cache_key(repo_url, kind, depth, expertise)
    expires = time.time() + ttl
    with _db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO cache_entries (cache_key, content, repo_url, kind, created_at, expires_at, hit_count)
               VALUES (?, ?, ?, ?, unixepoch(), ?, 0)""",
            (key, content, repo_url, kind, expires)
        )


def get_cache_stats() -> dict:
    """Return cache statistics."""
    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM cache_entries WHERE expires_at > ?", (time.time(),)).fetchone()[0]
        expired = total - active
        total_hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) FROM cache_entries").fetchone()[0]
        top_entries = conn.execute(
            "SELECT repo_url, kind, hit_count FROM cache_entries WHERE expires_at > ? ORDER BY hit_count DESC LIMIT 10",
            (time.time(),)
        ).fetchall()
        db_size = os.path.getsize(CACHE_DB_PATH) if os.path.exists(CACHE_DB_PATH) else 0
    return {
        "total_entries": total,
        "active_entries": active,
        "expired_entries": expired,
        "total_hits": total_hits,
        "db_size_bytes": db_size,
        "top_entries": [{"repo_url": r["repo_url"], "kind": r["kind"], "hits": r["hit_count"]} for r in top_entries],
    }


def cleanup_expired():
    """Remove expired entries."""
    with _db() as conn:
        deleted = conn.execute("DELETE FROM cache_entries WHERE expires_at <= ?", (time.time(),)).rowcount
    return deleted


# Init on import
init_cache()
