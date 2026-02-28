"""
RepoLM — Async Redis client with helpers for caching, rate limiting, sessions.
Python 3.9 compatible.
"""

import json
import logging
import os
import time
import zlib
from typing import Any, Optional

logger = logging.getLogger("repolm")

_redis = None

try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False


async def init_redis(redis_url: str = None):
    """Initialize async Redis connection. Call once at startup."""
    global _redis
    if not HAS_REDIS:
        logger.warning("redis package not installed, Redis disabled")
        return
    url = redis_url or os.environ.get("REDIS_URL", "")
    if not url:
        logger.info("REDIS_URL not set, Redis disabled")
        return
    _redis = aioredis.from_url(url, decode_responses=False, max_connections=20)
    # Test connection
    try:
        await _redis.ping()
        logger.info("Redis connected: %s", url.split("@")[-1] if "@" in url else url)
    except Exception:
        logger.exception("Redis connection failed")
        _redis = None


async def close_redis():
    global _redis
    if _redis:
        await _redis.close()
        _redis = None
        logger.info("Redis connection closed")


def is_available() -> bool:
    return _redis is not None


# ── Generic Key-Value with TTL ──

async def get(key: str) -> Optional[bytes]:
    if not _redis:
        return None
    try:
        return await _redis.get(key)
    except Exception:
        logger.exception("Redis GET failed: %s", key)
        return None


async def set(key: str, value: bytes, ttl: int = 3600):
    if not _redis:
        return
    try:
        await _redis.set(key, value, ex=ttl)
    except Exception:
        logger.exception("Redis SET failed: %s", key)


async def delete(key: str):
    if not _redis:
        return
    try:
        await _redis.delete(key)
    except Exception:
        logger.exception("Redis DELETE failed: %s", key)


# ── JSON helpers ──

async def get_json(key: str) -> Optional[Any]:
    data = await get(key)
    if data is None:
        return None
    try:
        return json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


async def set_json(key: str, value: Any, ttl: int = 3600):
    await set(key, json.dumps(value).encode(), ttl=ttl)


# ── Compressed blob helpers (for large repo data) ──

async def get_compressed(key: str) -> Optional[bytes]:
    """Get and decompress a zlib-compressed value."""
    data = await get(key)
    if data is None:
        return None
    try:
        return zlib.decompress(data)
    except Exception:
        return data  # might not be compressed


async def set_compressed(key: str, value: bytes, ttl: int = 7200):
    """Compress and store a value."""
    compressed = zlib.compress(value, level=6)
    await set(key, compressed, ttl=ttl)


# ── Repo data (special handling: large, mixed JSON + compressed blobs) ──

async def cache_repo(repo_id: str, repo_data: dict, ttl: int = 7200):
    """Cache repo data in Redis. Stores metadata as JSON, text as compressed blob."""
    if not _redis:
        return
    try:
        # Store metadata (status, message, data) as JSON
        meta = {
            "status": repo_data.get("status", "ready"),
            "message": repo_data.get("message", ""),
            "data": repo_data.get("data", {}),
        }
        await set_json("repo:meta:{0}".format(repo_id), meta, ttl=ttl)
        # Store files list compressed
        files = repo_data.get("files", [])
        if files:
            await set_compressed("repo:files:{0}".format(repo_id),
                                 json.dumps(files).encode(), ttl=ttl)
        # Store text compressed
        text = repo_data.get("text", "")
        if text:
            await set_compressed("repo:text:{0}".format(repo_id),
                                 text.encode(), ttl=ttl)
    except Exception:
        logger.exception("Redis cache_repo failed: %s", repo_id)


async def load_repo(repo_id: str) -> Optional[dict]:
    """Load repo data from Redis. Returns dict or None."""
    if not _redis:
        return None
    try:
        meta = await get_json("repo:meta:{0}".format(repo_id))
        if meta is None:
            return None
        files_raw = await get_compressed("repo:files:{0}".format(repo_id))
        files = json.loads(files_raw.decode()) if files_raw else []
        text_raw = await get_compressed("repo:text:{0}".format(repo_id))
        text = text_raw.decode() if text_raw else ""
        return {
            "status": meta.get("status", "ready"),
            "message": meta.get("message", ""),
            "data": meta.get("data", {}),
            "files": files,
            "text": text,
        }
    except Exception:
        logger.exception("Redis load_repo failed: %s", repo_id)
        return None


async def delete_repo(repo_id: str):
    """Remove repo data from Redis."""
    if not _redis:
        return
    for suffix in ("meta", "files", "text"):
        await delete("repo:{0}:{1}".format(suffix, repo_id))


# ── Rate Limiting (atomic) ──

async def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    """Returns True if rate limited. Uses sliding window counter."""
    if not _redis:
        return False
    try:
        rkey = "rl:{0}".format(key)
        now = time.time()
        pipe = _redis.pipeline()
        pipe.zremrangebyscore(rkey, 0, now - window_seconds)
        pipe.zadd(rkey, {str(now): now})
        pipe.zcard(rkey)
        pipe.expire(rkey, window_seconds)
        results = await pipe.execute()
        count = results[2]
        return count > max_requests
    except Exception:
        logger.exception("Redis rate limit check failed")
        return False


# ── Session caching (hot path) ──

async def cache_session(token: str, user_data: dict, ttl: int = 300):
    """Cache session → user lookup for 5 min."""
    await set_json("sess:{0}".format(token), user_data, ttl=ttl)


async def get_cached_session(token: str) -> Optional[dict]:
    """Get cached session user data."""
    return await get_json("sess:{0}".format(token))


async def invalidate_session(token: str):
    """Remove session from cache."""
    await delete("sess:{0}".format(token))
