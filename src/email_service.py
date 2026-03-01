"""
RepoLM — Email service using SendGrid.
Sends verification emails, welcome emails, generation-ready notifications, and weekly digests.
Falls back to SMTP if SendGrid is not configured, or logs if neither is configured.
"""

import os
import json
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from urllib.request import Request as URLRequest, urlopen
from urllib.error import URLError

logger = logging.getLogger("repolm")

# SendGrid config
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")

# SMTP fallback config
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")

FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@repolm.com")
FROM_NAME = os.environ.get("FROM_NAME", "RepoLM")
BASE_URL = os.environ.get("BASE_URL", "https://repolm.com")


def _sendgrid_configured():
    return bool(SENDGRID_API_KEY)


def _smtp_configured():
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def _send_via_sendgrid(to: str, subject: str, html: str, text: Optional[str] = None) -> bool:
    """Send email via SendGrid v3 API using stdlib only (no SDK needed)."""
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": FROM_EMAIL, "name": FROM_NAME},
        "subject": subject,
        "content": [],
    }
    if text:
        payload["content"].append({"type": "text/plain", "value": text})
    payload["content"].append({"type": "text/html", "value": html})

    data = json.dumps(payload).encode("utf-8")
    req = URLRequest(
        "https://api.sendgrid.com/v3/mail/send",
        data=data,
        headers={
            "Authorization": "Bearer " + SENDGRID_API_KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        resp = urlopen(req, timeout=10)
        status = resp.getcode()
        if status in (200, 201, 202):
            logger.info("SendGrid email sent: %s -> %s (status %d)", subject, to, status)
            return True
        else:
            logger.error("SendGrid unexpected status %d: %s -> %s", status, subject, to)
            return False
    except URLError as e:
        logger.error("SendGrid email failed: %s -> %s: %s", subject, to, e)
        return False


def _send_via_smtp(to: str, subject: str, html: str, text: Optional[str] = None) -> bool:
    """Send email via SMTP (fallback)."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = FROM_EMAIL
        msg["To"] = to
        if text:
            msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            if SMTP_PORT != 25:
                server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        logger.info("SMTP email sent: %s -> %s", subject, to)
        return True
    except Exception as e:
        logger.error("SMTP email failed: %s -> %s: %s", subject, to, e)
        return False


def send_email(to: str, subject: str, html: str, text: Optional[str] = None) -> bool:
    """Send an email. Tries SendGrid first, falls back to SMTP, logs if neither configured."""
    if not to:
        return False
    if _sendgrid_configured():
        return _send_via_sendgrid(to, subject, html, text)
    if _smtp_configured():
        return _send_via_smtp(to, subject, html, text)
    logger.debug("Email not sent (no provider configured): %s -> %s", subject, to)
    return False


# ── Email Templates ──────────────────────────────────────────────────────────

def _wrap_email(content: str) -> str:
    """Wrap content in a consistent email template."""
    return f"""
    <div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:0 auto;background:#09090b;color:#e5e7eb;padding:40px;border-radius:16px">
        {content}
        <p style="color:#6b7280;font-size:12px;margin-top:32px;border-top:1px solid #1f2937;padding-top:16px">
            <a href="{BASE_URL}" style="color:#a78bfa;text-decoration:none">RepoLM</a> — Understand any codebase in minutes.
            <br><a href="{BASE_URL}/app?settings=email" style="color:#6b7280">Manage email preferences</a>
        </p>
    </div>
    """


def send_verification(to: str, username: str, token: str) -> bool:
    """Send email verification link."""
    verify_url = f"{BASE_URL}/auth/verify?token={token}"
    html = _wrap_email(f"""
        <h1 style="color:#a78bfa;margin-bottom:8px">Verify your email ✉️</h1>
        <p>Hey {username},</p>
        <p>Click the button below to verify your email and activate your account:</p>
        <a href="{verify_url}" style="display:inline-block;background:#7c3aed;color:white;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:600;margin:20px 0">Verify Email →</a>
        <p style="color:#6b7280;font-size:13px">Or copy this link: <code style="background:#1f2937;padding:2px 6px;border-radius:4px;font-size:12px;word-break:break-all">{verify_url}</code></p>
        <p style="color:#6b7280;font-size:13px">This link expires in 24 hours. If you didn't sign up, ignore this email.</p>
    """)
    text = f"Hey {username}, verify your email at: {verify_url}"
    return send_email(to, "Verify your RepoLM email", html, text)


def send_welcome(to: str, username: str) -> bool:
    """Send welcome email after signup."""
    html = _wrap_email(f"""
        <h1 style="color:#a78bfa;margin-bottom:8px">Welcome to RepoLM! 🎉</h1>
        <p>Hey {username},</p>
        <p>You've got <strong style="color:#facc15">10 free tokens</strong> to get started. That's enough for an overview + a few chats.</p>
        <h3 style="color:#a78bfa">Here's what you can do:</h3>
        <ul>
            <li>📖 <strong>Overview</strong> — architecture breakdown of any repo</li>
            <li>🎙️ <strong>Podcast</strong> — two AI hosts explain the code</li>
            <li>📊 <strong>Slides</strong> — presentation-ready deck</li>
            <li>💬 <strong>Chat</strong> — ask questions about the codebase</li>
        </ul>
        <a href="{BASE_URL}/app" style="display:inline-block;background:#7c3aed;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px">Open RepoLM →</a>
    """)
    return send_email(to, "Welcome to RepoLM! 🎉", html)


def send_generation_ready(to: str, username: str, repo_name: str, kind: str) -> bool:
    """Send notification when generation completes."""
    html = _wrap_email(f"""
        <h2 style="color:#a78bfa">Your {kind} is ready! ✨</h2>
        <p>Hey {username}, your <strong>{kind}</strong> for <strong>{repo_name}</strong> has been generated.</p>
        <a href="{BASE_URL}/app" style="display:inline-block;background:#7c3aed;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px">View it now →</a>
    """)
    return send_email(to, f"Your {kind} for {repo_name} is ready!", html)


def send_weekly_digest(to: str, username: str, stats: dict) -> bool:
    """Send weekly activity digest."""
    repos = stats.get("repos_this_week", 0)
    new_features = stats.get("new_features", "")
    html = _wrap_email(f"""
        <h2 style="color:#a78bfa">Your Weekly RepoLM Digest 📊</h2>
        <p>Hey {username}, here's what happened this week:</p>
        <div style="background:#111827;border:1px solid #1f2937;border-radius:12px;padding:20px;margin:16px 0">
            <p style="font-size:24px;color:#facc15;margin:0"><strong>{repos}</strong> repos analyzed this week</p>
        </div>
        {new_features}
        <a href="{BASE_URL}/app" style="display:inline-block;background:#7c3aed;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px">Explore more repos →</a>
    """)
    return send_email(to, "Your Weekly RepoLM Digest 📊", html)
