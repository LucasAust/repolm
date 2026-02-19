"""
RepoLM — Concurrency management: thread pools, semaphores, job queues.
Handles graceful degradation under load.
"""

import asyncio
import logging
import os
import time
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("repolm")

# ── Thread Pools ──
INGEST_WORKERS = int(os.environ.get("INGEST_WORKERS", "8"))
GENERATE_WORKERS = int(os.environ.get("GENERATE_WORKERS", "12"))
AUDIO_WORKERS = int(os.environ.get("AUDIO_WORKERS", "4"))

ingest_pool = ThreadPoolExecutor(max_workers=INGEST_WORKERS, thread_name_prefix="ingest")
generate_pool = ThreadPoolExecutor(max_workers=GENERATE_WORKERS, thread_name_prefix="generate")
audio_pool = ThreadPoolExecutor(max_workers=AUDIO_WORKERS, thread_name_prefix="audio")

# ── SSE Semaphore ──
sse_semaphore = asyncio.Semaphore(int(os.environ.get("MAX_SSE_STREAMS", "20")))

# ── Per-IP Limits ──
_ip_sse_counts: Dict[str, int] = {}
_ip_ingest_counts: Dict[str, int] = {}
_ip_lock = threading.Lock()

MAX_SSE_PER_IP = 3
MAX_INGEST_PER_IP = 2


class IPLimitContext:
    """Context manager for per-IP concurrency tracking."""
    def __init__(self, counter_dict: dict, ip: str, max_count: int):
        self._dict = counter_dict
        self._ip = ip
        self._max = max_count
        self._acquired = False

    def try_acquire(self) -> bool:
        with _ip_lock:
            current = self._dict.get(self._ip, 0)
            if current >= self._max:
                return False
            self._dict[self._ip] = current + 1
            self._acquired = True
            return True

    def release(self):
        if self._acquired:
            with _ip_lock:
                current = self._dict.get(self._ip, 1)
                if current <= 1:
                    self._dict.pop(self._ip, None)
                else:
                    self._dict[self._ip] = current - 1
                self._acquired = False


def acquire_sse(ip: str) -> Optional[IPLimitContext]:
    ctx = IPLimitContext(_ip_sse_counts, ip, MAX_SSE_PER_IP)
    if ctx.try_acquire():
        return ctx
    return None


def acquire_ingest(ip: str) -> Optional[IPLimitContext]:
    ctx = IPLimitContext(_ip_ingest_counts, ip, MAX_INGEST_PER_IP)
    if ctx.try_acquire():
        return ctx
    return None


# ── Job Queue with Position Tracking ──
MAX_QUEUE_DEPTH = 50


class JobQueue:
    """FIFO queue with position tracking for when pools are near capacity."""

    def __init__(self, pool: ThreadPoolExecutor, pool_name: str, max_workers: int):
        self._pool = pool
        self._pool_name = pool_name
        self._max_workers = max_workers
        self._lock = threading.Lock()
        self._queue: OrderedDict[str, dict] = OrderedDict()  # job_id -> {fn, args, status, position}
        self._active_count = 0

    @property
    def utilization(self) -> float:
        """Current pool utilization 0.0-1.0."""
        with self._lock:
            return self._active_count / self._max_workers if self._max_workers else 0.0

    @property
    def queue_length(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active_count

    def submit(self, job_id: str, fn: Callable, *args) -> Tuple[str, Optional[int]]:
        """
        Submit a job. Returns (status, queue_position).
        status: "running" if started immediately, "queued" if queued, "rejected" if full.
        queue_position: None if running, 1-based if queued, None if rejected.
        """
        with self._lock:
            util = self._active_count / self._max_workers if self._max_workers else 1.0
            if util < 0.8:
                # Start immediately
                self._active_count += 1
                self._pool.submit(self._wrap, job_id, fn, *args)
                return "running", None
            elif len(self._queue) < MAX_QUEUE_DEPTH:
                # Queue it
                self._queue[job_id] = {"fn": fn, "args": args}
                pos = len(self._queue)
                return "queued", pos
            else:
                return "rejected", None

    def _wrap(self, job_id: str, fn: Callable, *args):
        """Wrapper that decrements active count and processes queue on completion."""
        try:
            fn(*args)
        except Exception:
            logger.exception("Job %s failed in pool %s", job_id, self._pool_name)
        finally:
            self._on_complete()

    def _on_complete(self):
        """Called when a job finishes. Process next queued job if any."""
        with self._lock:
            self._active_count -= 1
            if self._queue:
                next_id, entry = self._queue.popitem(last=False)
                self._active_count += 1
                self._pool.submit(self._wrap, next_id, entry["fn"], *entry["args"])

    def get_position(self, job_id: str) -> Optional[int]:
        """Get current queue position (1-based) or None if not queued."""
        with self._lock:
            for i, qid in enumerate(self._queue.keys()):
                if qid == job_id:
                    return i + 1
            return None

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._queue:
                del self._queue[job_id]
                return True
            return False


# ── Queue instances ──
ingest_queue = JobQueue(ingest_pool, "ingest", INGEST_WORKERS)
generate_queue = JobQueue(generate_pool, "generate", GENERATE_WORKERS)
audio_queue = JobQueue(audio_pool, "audio", AUDIO_WORKERS)


def get_pool_status() -> dict:
    """Return current pool utilization for monitoring."""
    return {
        "ingest": {
            "active": ingest_queue.active_count,
            "max_workers": INGEST_WORKERS,
            "utilization": round(ingest_queue.utilization, 2),
            "queued": ingest_queue.queue_length,
        },
        "generate": {
            "active": generate_queue.active_count,
            "max_workers": GENERATE_WORKERS,
            "utilization": round(generate_queue.utilization, 2),
            "queued": generate_queue.queue_length,
        },
        "audio": {
            "active": audio_queue.active_count,
            "max_workers": AUDIO_WORKERS,
            "utilization": round(audio_queue.utilization, 2),
            "queued": audio_queue.queue_length,
        },
        "per_ip_sse": dict(_ip_sse_counts),
        "per_ip_ingest": dict(_ip_ingest_counts),
    }


def shutdown_pools():
    """Graceful shutdown of all pools."""
    for pool in (ingest_pool, generate_pool, audio_pool):
        pool.shutdown(wait=False)
