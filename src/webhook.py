"""
RepoLM — Webhook delivery for async job completion.
Fire-and-forget: logs failures, no retries.
"""

import hashlib
import hmac
import json
import logging
import asyncio
from typing import Optional

import httpx

logger = logging.getLogger("repolm")

# Reference to main event loop, set at startup (same pattern as db_async)
_main_loop = None  # type: Optional[asyncio.AbstractEventLoop]


def set_main_loop(loop):
    global _main_loop
    _main_loop = loop


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """HMAC-SHA256 signature of payload using the API key as secret."""
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


async def _deliver_webhook(webhook_url: str, payload: dict, api_key: str):
    """POST webhook payload to the registered URL. 10s timeout, log failures."""
    body = json.dumps(payload, separators=(",", ":"))
    signature = _sign_payload(body.encode("utf-8"), api_key)
    headers = {
        "Content-Type": "application/json",
        "X-RepoLM-Signature": signature,
        "User-Agent": "RepoLM-Webhook/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, content=body, headers=headers)
            logger.info("Webhook delivered to %s — status %d", webhook_url, resp.status_code)
    except Exception:
        logger.exception("Webhook delivery failed to %s", webhook_url)


def fire_webhook(webhook_url: str, payload: dict, api_key: str):
    """Fire-and-forget webhook from a sync background thread.

    Schedules delivery on the main event loop so it doesn't block the worker.
    """
    if not webhook_url:
        return
    loop = _main_loop
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(
            _deliver_webhook(webhook_url, payload, api_key), loop
        )
    else:
        logger.warning("Cannot fire webhook: no running event loop")


def build_completed_payload(job_id: str, result: str = None) -> dict:
    return {
        "event": "job.completed",
        "job_id": job_id,
        "status": "done",
        "result": result,
    }


def build_failed_payload(job_id: str, error: str = None) -> dict:
    return {
        "event": "job.failed",
        "job_id": job_id,
        "status": "error",
        "message": error,
    }
