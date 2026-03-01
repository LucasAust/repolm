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

import ipaddress
import socket

import httpx

logger = logging.getLogger("repolm")


def _validate_webhook_url(url: str):
    """Validate webhook URL: must be https and resolve to a public IP."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Webhook URL must use https:// scheme")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Webhook URL has no hostname")
    # Resolve hostname and check all addresses
    try:
        addrinfos = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("Cannot resolve webhook hostname: %s" % hostname)
    for family, _type, _proto, _canonname, sockaddr in addrinfos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local or ip.is_multicast:
            raise ValueError("Webhook URL resolves to a private/reserved IP: %s" % ip)

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
    try:
        _validate_webhook_url(webhook_url)
    except ValueError as e:
        logger.warning("Webhook URL rejected: %s — %s", webhook_url, e)
        return
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
