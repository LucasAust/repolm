"""
RepoLM — Shared state: in-memory stores with TTL, Redis-backed when available,
SQLite-backed repo cache as cold storage, LRU eviction, and disk cleanup.
"""

import asyncio
import logging
import os
import shutil
import sqlite3
import time
import threading
import zlib
import json
from contextlib import contextmanager
from typing import Any, Optional

logger = logging.getLogger("repolm")

_DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
REPO_CACHE_DB = os.path.join(_DATA_DIR, "repolm_repo_cache.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
MAX_DISK_USAGE_BYTES = 5 * 1024 * 1024 * 1024  # 5GB alert threshold


class TTLDict:
    """Dict with per-entry TTL and LRU eviction."""

    def __init__(self, default_ttl: int = 7200, name: str = "store", max_size: int = 0):
        self._data = {}
        self._expires = {}
        self._access_order = []  # for LRU
        self._lock = threading.Lock()
        self.default_ttl = default_ttl
        self.name = name
        self.max_size = max_size  # 0 = unlimited

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._data:
                if self._expires.get(key, float('inf')) > time.time():
                    # Update LRU
                    if key in self._access_order:
                        self._access_order.remove(key)
                    self._access_order.append(key)
                    return self._data[key]
                else:
                    del self._data[key]
                    del self._expires[key]
                    if key in self._access_order:
                        self._access_order.remove(key)
        return None

    def set(self, key: str, value: Any, ttl: int = None):
        with self._lock:
            # LRU eviction
            if self.max_size > 0 and key not in self._data and len(self._data) >= self.max_size:
                if self._access_order:
                    evict_key = self._access_order.pop(0)
                    self._data.pop(evict_key, None)
                    self._expires.pop(evict_key, None)
            self._data[key] = value
            self._expires[key] = time.time() + (ttl or self.default_ttl)
            if key in self._access_order:
                self._access_order.remove(key)
            self._access_order.append(key)

    def delete(self, key: str):
        with self._lock:
            self._data.pop(key, None)
            self._expires.pop(key, None)
            if key in self._access_order:
                self._access_order.remove(key)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __setitem__(self, key: str, value: Any):
        self.set(key, value)

    def cleanup(self) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            expired_keys = [k for k, exp in self._expires.items() if exp <= now]
            for k in expired_keys:
                del self._data[k]
                del self._expires[k]
                if k in self._access_order:
                    self._access_order.remove(k)
                removed += 1
        return removed

    def size(self) -> int:
        with self._lock:
            return len(self._data)


# ── Global Stores ──
# repos has LRU max_size=20 per worker
repos = TTLDict(default_ttl=7200, name="repos", max_size=20)
jobs = TTLDict(default_ttl=3600, name="jobs")
audio_jobs = TTLDict(default_ttl=3600, name="audio_jobs")
shared_content = TTLDict(default_ttl=86400, name="shared_content")


# ── SQLite Repo Cache (cross-worker shared) ──
def _init_repo_cache_db():
    conn = sqlite3.connect(REPO_CACHE_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS repo_cache (
            repo_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            message TEXT DEFAULT '',
            data_json TEXT,
            files_json BLOB,
            repo_text BLOB,
            created_at REAL DEFAULT (unixepoch()),
            accessed_at REAL DEFAULT (unixepoch())
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rc_accessed ON repo_cache(accessed_at)")
    conn.commit()
    conn.close()


_init_repo_cache_db()


def cache_repo_to_db(repo_id: str, repo_data: dict):
    """Store repo in SQLite with compressed text and files."""
    try:
        data_json = json.dumps(repo_data.get("data", {}))
        files_json = zlib.compress(json.dumps(repo_data.get("files", [])).encode())
        text_blob = zlib.compress((repo_data.get("text", "") or "").encode())
        conn = sqlite3.connect(REPO_CACHE_DB)
        conn.execute(
            """INSERT OR REPLACE INTO repo_cache (repo_id, status, message, data_json, files_json, repo_text, created_at, accessed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (repo_id, repo_data.get("status", "ready"), repo_data.get("message", ""),
             data_json, files_json, text_blob, time.time(), time.time())
        )
        conn.commit()
        conn.close()

        # Also cache to Redis if available
        try:
            import redis_client
            if redis_client.is_available():
                import asyncio as _aio
                try:
                    loop = _aio.get_running_loop()
                    loop.create_task(redis_client.cache_repo(repo_id, repo_data))
                except RuntimeError:
                    pass  # no event loop, skip Redis
        except ImportError:
            pass
    except Exception:
        logger.exception("Failed to cache repo %s to DB", repo_id)


def load_repo_from_db(repo_id: str) -> Optional[dict]:
    """Load repo from SQLite cache. Returns dict or None."""
    try:
        conn = sqlite3.connect(REPO_CACHE_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM repo_cache WHERE repo_id=?", (repo_id,)).fetchone()
        if not row:
            conn.close()
            return None
        conn.execute("UPDATE repo_cache SET accessed_at=? WHERE repo_id=?", (time.time(), repo_id))
        conn.commit()
        result = {
            "status": row["status"],
            "message": row["message"],
            "data": json.loads(row["data_json"]) if row["data_json"] else {},
            "files": json.loads(zlib.decompress(row["files_json"]).decode()) if row["files_json"] else [],
            "text": zlib.decompress(row["repo_text"]).decode() if row["repo_text"] else "",
        }
        conn.close()
        return result
    except Exception:
        logger.exception("Failed to load repo %s from DB", repo_id)
        return None


def get_repo_with_fallback(repo_id: str) -> Optional[dict]:
    """Try memory first, then SQLite cold storage. Redis is handled in db_async."""
    # 1. In-memory
    repo = repos.get(repo_id)
    if repo:
        return repo

    # 2. SQLite cold storage
    db_repo = load_repo_from_db(repo_id)
    if db_repo and db_repo["status"] == "ready":
        repos.set(repo_id, db_repo)
        return db_repo
    return None


def find_cached_repo_by_url(url: str) -> Optional[str]:
    """Find a recently cached repo_id by URL (< 2 hours old). Returns repo_id or None."""
    try:
        normalized = url.strip().lower().rstrip("/").replace(".git", "")
        conn = sqlite3.connect(REPO_CACHE_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT repo_id FROM repo_cache
               WHERE status='ready' AND json_extract(data_json, '$.url') LIKE ?
               AND accessed_at > ? ORDER BY accessed_at DESC LIMIT 1""",
            (f"%{normalized.split('github.com/')[-1]}%", time.time() - 7200)
        ).fetchone()
        conn.close()
        if row:
            return row["repo_id"]
    except Exception:
        logger.exception("find_cached_repo_by_url failed")
    return None


# ── Disk Cleanup ──
def cleanup_disk() -> dict:
    """Clean up old audio, clone, and PPTX files. Returns summary."""
    cleaned = {"audio_dirs": 0, "clone_dirs": 0, "pptx_files": 0, "bytes_freed": 0}
    now = time.time()

    # Audio dirs older than 2 hours
    if os.path.exists(OUTPUT_DIR):
        for entry in os.listdir(OUTPUT_DIR):
            path = os.path.join(OUTPUT_DIR, entry)
            if os.path.isdir(path):
                try:
                    mtime = os.path.getmtime(path)
                    if now - mtime > 7200:  # 2 hours
                        size = _dir_size(path)
                        shutil.rmtree(path, ignore_errors=True)
                        cleaned["audio_dirs"] += 1
                        cleaned["bytes_freed"] += size
                except Exception:
                    pass

    # PPTX files older than 1 hour
    if os.path.exists(OUTPUT_DIR):
        for entry in os.listdir(OUTPUT_DIR):
            path = os.path.join(OUTPUT_DIR, entry)
            if path.endswith(".pptx") and os.path.isfile(path):
                try:
                    if now - os.path.getmtime(path) > 3600:
                        size = os.path.getsize(path)
                        os.remove(path)
                        cleaned["pptx_files"] += 1
                        cleaned["bytes_freed"] += size
                except Exception:
                    pass

    # Clone dirs in /tmp older than 30 minutes
    import tempfile
    tmp = tempfile.gettempdir()
    for entry in os.listdir(tmp):
        if entry.startswith("repolm_") or entry.startswith("tmp"):
            path = os.path.join(tmp, entry)
            if os.path.isdir(path):
                try:
                    if now - os.path.getmtime(path) > 1800:
                        size = _dir_size(path)
                        shutil.rmtree(path, ignore_errors=True)
                        cleaned["clone_dirs"] += 1
                        cleaned["bytes_freed"] += size
                except Exception:
                    pass

    if any(v > 0 for v in cleaned.values()):
        logger.info("[disk_cleanup] %s", cleaned)
    return cleaned


def get_disk_usage() -> dict:
    """Get disk usage info for health endpoint."""
    output_size = _dir_size(OUTPUT_DIR) if os.path.exists(OUTPUT_DIR) else 0
    cache_db_size = os.path.getsize(REPO_CACHE_DB) if os.path.exists(REPO_CACHE_DB) else 0
    total = output_size + cache_db_size
    return {
        "output_dir_bytes": output_size,
        "repo_cache_db_bytes": cache_db_size,
        "total_bytes": total,
        "alert": total > MAX_DISK_USAGE_BYTES,
        "max_bytes": MAX_DISK_USAGE_BYTES,
    }


def _dir_size(path: str) -> int:
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except Exception:
        pass
    return total


# ── Background Cleanup Task ──
async def cleanup_stores():
    """Background task: clean up expired entries every 10 minutes, disk every 30 min."""
    cycle = 0
    while True:
        await asyncio.sleep(600)
        cycle += 1
        total = 0
        for store in [repos, jobs, audio_jobs, shared_content]:
            total += store.cleanup()
        try:
            import db as _sync_db
            _sync_db.cleanup_old_jobs(max_age_hours=24)
            _sync_db.cleanup_rate_limits()
        except Exception:
            pass
        # Disk cleanup every 30 min (every 3 cycles)
        # Run in executor to avoid blocking the event loop (os.walk + shutil.rmtree)
        if cycle % 3 == 0:
            try:
                import asyncio as _aio
                await _aio.get_event_loop().run_in_executor(None, cleanup_disk)
            except Exception:
                logger.exception("Disk cleanup failed")
        if total:
            logger.info("[cleanup] Removed %d expired in-memory entries", total)
