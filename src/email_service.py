"""
RepoLM — Email service using stdlib smtplib.
Sends welcome emails, generation-ready notifications, and weekly digests.
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

logger = logging.getLogger("repolm")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@repolm.com")
BASE_URL = os.environ.get("BASE_URL", "https://repolm.com")


def _is_configured():
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def send_email(to: str, subject: str, html: str, text: Optional[str] = None):
    """Send an email. Fails silently if SMTP not configured."""
    if not _is_configured():
        logger.debug("Email not sent (SMTP not configured): %s -> %s", subject, to)
        return False
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
        logger.info("Email sent: %s -> %s", subject, to)
        return True
    except Exception as e:
        logger.error("Email send failed: %s -> %s: %s", subject, to, e)
        return False


def send_welcome(to: str, username: str):
    """Send welcome email after signup."""
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:600px;margin:0 auto;background:#09090b;color:#e5e7eb;padding:40px;border-radius:16px">
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
        <p style="color:#6b7280;font-size:12px;margin-top:32px">You're receiving this because you signed up at RepoLM. <a href="{BASE_URL}/app?settings=email" style="color:#6b7280">Manage preferences</a></p>
    </div>
    """
    send_email(to, "Welcome to RepoLM! 🎉", html)


def send_generation_ready(to: str, username: str, repo_name: str, kind: str):
    """Send notification when generation completes."""
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:600px;margin:0 auto;background:#09090b;color:#e5e7eb;padding:40px;border-radius:16px">
        <h2 style="color:#a78bfa">Your {kind} is ready! ✨</h2>
        <p>Hey {username}, your <strong>{kind}</strong> for <strong>{repo_name}</strong> has been generated.</p>
        <a href="{BASE_URL}/app" style="display:inline-block;background:#7c3aed;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px">View it now →</a>
        <p style="color:#6b7280;font-size:12px;margin-top:32px"><a href="{BASE_URL}/app?settings=email" style="color:#6b7280">Unsubscribe from notifications</a></p>
    </div>
    """
    send_email(to, f"Your {kind} for {repo_name} is ready!", html)


def send_weekly_digest(to: str, username: str, stats: dict):
    """Send weekly activity digest."""
    repos = stats.get("repos_this_week", 0)
    new_features = stats.get("new_features", "")
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:600px;margin:0 auto;background:#09090b;color:#e5e7eb;padding:40px;border-radius:16px">
        <h2 style="color:#a78bfa">Your Weekly RepoLM Digest 📊</h2>
        <p>Hey {username}, here's what happened this week:</p>
        <div style="background:#111827;border:1px solid #1f2937;border-radius:12px;padding:20px;margin:16px 0">
            <p style="font-size:24px;color:#facc15;margin:0"><strong>{repos}</strong> repos analyzed this week</p>
        </div>
        {new_features}
        <a href="{BASE_URL}/app" style="display:inline-block;background:#7c3aed;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px">Explore more repos →</a>
        <p style="color:#6b7280;font-size:12px;margin-top:32px"><a href="{BASE_URL}/app?settings=email" style="color:#6b7280">Unsubscribe</a></p>
    </div>
    """
    send_email(to, "Your Weekly RepoLM Digest 📊", html)
