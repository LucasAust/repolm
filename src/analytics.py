"""
RepoLM — Lightweight event tracking to SQLite.
Tracks usage for internal analytics: repos ingested, content generated,
audio generated, concept lab, shares, API calls, conversion funnel.
"""

import os
import json
import time
import sqlite3
from contextlib import contextmanager
from typing import Optional

_DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
ANALYTICS_DB_PATH = os.path.join(_DATA_DIR, "repolm_analytics.db")


def _get_db():
    conn = sqlite3.connect(ANALYTICS_DB_PATH)
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


def init_analytics():
    """Create analytics tables."""
    with _db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            user_id INTEGER,
            data TEXT,
            created_at REAL DEFAULT (unixepoch())
        );
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id);
        CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);

        CREATE TABLE IF NOT EXISTS daily_rollups (
            date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (date, event_type)
        );
        """)


def track(event_type: str, user_id: Optional[int] = None, data: dict = None):
    """Track an analytics event."""
    with _db() as conn:
        conn.execute(
            "INSERT INTO events (event_type, user_id, data) VALUES (?,?,?)",
            (event_type, user_id, json.dumps(data) if data else None)
        )


def get_stats(days: int = 30) -> dict:
    """Get aggregated analytics stats."""
    cutoff = time.time() - (days * 86400)
    with _db() as conn:
        # Event counts by type
        rows = conn.execute(
            "SELECT event_type, COUNT(*) as cnt FROM events WHERE created_at > ? GROUP BY event_type ORDER BY cnt DESC",
            (cutoff,)
        ).fetchall()
        event_counts = {r["event_type"]: r["cnt"] for r in rows}

        # Unique users
        unique_users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM events WHERE user_id IS NOT NULL AND created_at > ?",
            (cutoff,)
        ).fetchone()[0]

        # Popular repos (top 10)
        popular = conn.execute("""
            SELECT json_extract(data, '$.url') as url, COUNT(*) as cnt
            FROM events WHERE event_type='repo_ingested' AND created_at > ?
            GROUP BY url ORDER BY cnt DESC LIMIT 10
        """, (cutoff,)).fetchall()

        # Conversion funnel
        signups = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='signup' AND created_at > ?", (cutoff,)).fetchone()[0]
        first_gen = conn.execute("SELECT COUNT(DISTINCT user_id) FROM events WHERE event_type='content_generated' AND created_at > ?", (cutoff,)).fetchone()[0]
        first_purchase = conn.execute("SELECT COUNT(DISTINCT user_id) FROM events WHERE event_type='purchase' AND created_at > ?", (cutoff,)).fetchone()[0]

        # Daily trend (last 7 days)
        daily = conn.execute("""
            SELECT date(created_at, 'unixepoch') as day, COUNT(*) as cnt
            FROM events WHERE created_at > ? GROUP BY day ORDER BY day DESC LIMIT 7
        """, (cutoff,)).fetchall()

    return {
        "period_days": days,
        "event_counts": event_counts,
        "unique_users": unique_users,
        "popular_repos": [{"url": r["url"], "count": r["cnt"]} for r in popular],
        "funnel": {"signups": signups, "first_generate": first_gen, "first_purchase": first_purchase},
        "daily_trend": [{"date": r["day"], "events": r["cnt"]} for r in daily],
    }


def rollup_daily():
    """Create daily rollup for today. Call from a periodic task."""
    today = time.strftime("%Y-%m-%d")
    start = time.time() - 86400
    with _db() as conn:
        rows = conn.execute(
            "SELECT event_type, COUNT(*) as cnt FROM events WHERE created_at > ? GROUP BY event_type",
            (start,)
        ).fetchall()
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO daily_rollups (date, event_type, count) VALUES (?,?,?)",
                (today, r["event_type"], r["cnt"])
            )


# Init on import
init_analytics()
