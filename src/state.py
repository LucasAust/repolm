"""
RepoLM — Shared state: in-memory stores with TTL, LRU eviction, and disk cleanup.
Repo cache persistence is handled by db_postgres (Postgres) and redis_client.
"""

import asyncio
import logging
import os
import shutil
import time
import threading
import json
from typing import Any, Optional

logger = logging.getLogger("repolm")

_DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
MAX_DISK_USAGE_BYTES = 5 * 1024 * 1024 * 1024  # 5GB alert threshold


class TTLDict:
    """Dict with per-entry TTL and LRU eviction."""

    def __init__(self, default_ttl=7200, name="store", max_size=0):
        self._data = {}
        self._expires = {}
        self._access_order = []  # for LRU
        self._lock = threading.Lock()
        self.default_ttl = default_ttl
        self.name = name
        self.max_size = max_size  # 0 = unlimited

    def get(self, key):
        # type: (str) -> Optional[Any]
        with self._lock:
            if key in self._data:
                if self._expires.get(key, float('inf')) > time.time():
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

    def set(self, key, value, ttl=None):
        # type: (str, Any, Optional[int]) -> None
        with self._lock:
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

    def delete(self, key):
        with self._lock:
            self._data.pop(key, None)
            self._expires.pop(key, None)
            if key in self._access_order:
                self._access_order.remove(key)

    def __contains__(self, key):
        return self.get(key) is not None

    def __getitem__(self, key):
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __setitem__(self, key, value):
        self.set(key, value)

    def cleanup(self):
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

    def size(self):
        with self._lock:
            return len(self._data)


# ── Global Stores ──
# repos has LRU max_size=20 per worker
repos = TTLDict(default_ttl=7200, name="repos", max_size=20)
jobs = TTLDict(default_ttl=3600, name="jobs")
audio_jobs = TTLDict(default_ttl=3600, name="audio_jobs")
shared_content = TTLDict(default_ttl=86400, name="shared_content")


# ── Disk Cleanup ──
def cleanup_disk():
    """Clean up old audio, clone, and PPTX files. Returns summary."""
    cleaned = {"audio_dirs": 0, "clone_dirs": 0, "pptx_files": 0, "bytes_freed": 0}
    now = time.time()

    if os.path.exists(OUTPUT_DIR):
        for entry in os.listdir(OUTPUT_DIR):
            path = os.path.join(OUTPUT_DIR, entry)
            if os.path.isdir(path):
                try:
                    mtime = os.path.getmtime(path)
                    if now - mtime > 7200:
                        size = _dir_size(path)
                        shutil.rmtree(path, ignore_errors=True)
                        cleaned["audio_dirs"] += 1
                        cleaned["bytes_freed"] += size
                except Exception:
                    pass

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


def get_disk_usage():
    """Get disk usage info for health endpoint."""
    output_size = _dir_size(OUTPUT_DIR) if os.path.exists(OUTPUT_DIR) else 0
    return {
        "output_dir_bytes": output_size,
        "total_bytes": output_size,
        "alert": output_size > MAX_DISK_USAGE_BYTES,
        "max_bytes": MAX_DISK_USAGE_BYTES,
    }


def _dir_size(path):
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
        if cycle % 3 == 0:
            try:
                await asyncio.get_event_loop().run_in_executor(None, cleanup_disk)
            except Exception:
                logger.exception("Disk cleanup failed")
        if total:
            logger.info("[cleanup] Removed %d expired in-memory entries", total)
