#!/usr/bin/env python3
"""
One-time migration: SQLite → PostgreSQL.
Reads all data from SQLite DBs and inserts into Postgres.

Usage:
    DATABASE_URL=postgresql://... python3 migrate_sqlite_to_pg.py

Requires: asyncpg, plus the existing SQLite DBs in DATA_DIR.
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
import zlib

import asyncpg

# Paths
_DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
MAIN_DB = os.path.join(_DATA_DIR, "repolm.db")
CACHE_DB = os.path.join(_DATA_DIR, "repolm_repo_cache.db")


async def migrate():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    # Initialize PG tables first
    import db_postgres
    await db_postgres.init_pool(url)
    pool = db_postgres._get_pool()

    # --- Main DB ---
    if os.path.exists(MAIN_DB):
        print(f"Migrating main DB: {MAIN_DB}")
        conn_sq = sqlite3.connect(MAIN_DB)
        conn_sq.row_factory = sqlite3.Row

        async with pool.acquire() as pg:
            # Users
            rows = conn_sq.execute("SELECT * FROM users").fetchall()
            print(f"  users: {len(rows)}")
            for r in rows:
                d = dict(r)
                await pg.execute("""
                    INSERT INTO users (id, github_id, username, email, avatar_url, created_at, last_login,
                        stripe_customer_id, subscription_status, subscription_id, plan,
                        repos_this_month, month_reset, tokens, has_purchased,
                        password_hash, password_salt, referral_code, referred_by,
                        api_key, api_calls_today, api_calls_date)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22)
                    ON CONFLICT (id) DO NOTHING
                """, d["id"], d.get("github_id"), d["username"], d.get("email"), d.get("avatar_url"),
                    d.get("created_at"), d.get("last_login"),
                    d.get("stripe_customer_id"), d.get("subscription_status", "none"),
                    d.get("subscription_id"), d.get("plan", "free"),
                    d.get("repos_this_month", 0), d.get("month_reset"),
                    d.get("tokens", 0), d.get("has_purchased", 0),
                    d.get("password_hash"), d.get("password_salt"),
                    d.get("referral_code"), d.get("referred_by"),
                    d.get("api_key"), d.get("api_calls_today", 0), d.get("api_calls_date"))

            # Reset sequence
            max_id = await pg.fetchval("SELECT COALESCE(MAX(id), 0) FROM users")
            await pg.execute(f"SELECT setval('users_id_seq', {max_id})")

            # Sessions
            rows = conn_sq.execute("SELECT * FROM sessions").fetchall()
            print(f"  sessions: {len(rows)}")
            for r in rows:
                d = dict(r)
                try:
                    await pg.execute(
                        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                        d["token"], d["user_id"], d.get("created_at"), d.get("expires_at"))
                except Exception as e:
                    print(f"    skip session: {e}")

            # Repos
            rows = conn_sq.execute("SELECT * FROM repos").fetchall()
            print(f"  repos: {len(rows)}")
            for r in rows:
                d = dict(r)
                blob = d.get("repo_text_z")
                if blob and isinstance(blob, (bytes, memoryview)):
                    blob = bytes(blob)
                else:
                    blob = None
                try:
                    await pg.execute("""
                        INSERT INTO repos (id, user_id, url, name, repo_hash, tree, file_count, total_chars,
                            languages, repo_text_z, file_index, created_at, last_accessed)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                        ON CONFLICT DO NOTHING
                    """, d["id"], d["user_id"], d["url"], d["name"], d["repo_hash"],
                        d.get("tree"), d.get("file_count"), d.get("total_chars"),
                        d.get("languages"), blob, d.get("file_index"),
                        d.get("created_at"), d.get("last_accessed"))
                except Exception as e:
                    print(f"    skip repo {d['id']}: {e}")

            max_id = await pg.fetchval("SELECT COALESCE(MAX(id), 0) FROM repos")
            await pg.execute(f"SELECT setval('repos_id_seq', {max_id})")

            # Generated
            rows = conn_sq.execute("SELECT * FROM generated").fetchall()
            print(f"  generated: {len(rows)}")
            for r in rows:
                d = dict(r)
                try:
                    await pg.execute("""
                        INSERT INTO generated (id, repo_id, kind, depth, expertise, content, created_at)
                        VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING
                    """, d["id"], d["repo_id"], d["kind"], d["depth"], d["expertise"],
                        d["content"], d.get("created_at"))
                except Exception as e:
                    print(f"    skip generated {d['id']}: {e}")

            max_id = await pg.fetchval("SELECT COALESCE(MAX(id), 0) FROM generated")
            await pg.execute(f"SELECT setval('generated_id_seq', {max_id})")

            # Chats
            rows = conn_sq.execute("SELECT * FROM chats").fetchall()
            print(f"  chats: {len(rows)}")
            for r in rows:
                d = dict(r)
                try:
                    await pg.execute("""
                        INSERT INTO chats (id, repo_id, role, message, selection, file_path, created_at)
                        VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING
                    """, d["id"], d["repo_id"], d["role"], d["message"],
                        d.get("selection"), d.get("file_path"), d.get("created_at"))
                except Exception as e:
                    print(f"    skip chat {d['id']}: {e}")

            max_id = await pg.fetchval("SELECT COALESCE(MAX(id), 0) FROM chats")
            await pg.execute(f"SELECT setval('chats_id_seq', {max_id})")

            # Token transactions
            rows = conn_sq.execute("SELECT * FROM token_transactions").fetchall()
            print(f"  token_transactions: {len(rows)}")
            for r in rows:
                d = dict(r)
                try:
                    await pg.execute("""
                        INSERT INTO token_transactions (id, user_id, amount, action, description, created_at)
                        VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING
                    """, d["id"], d["user_id"], d["amount"], d["action"],
                        d.get("description"), d.get("created_at"))
                except Exception as e:
                    print(f"    skip tx {d['id']}: {e}")

            max_id = await pg.fetchval("SELECT COALESCE(MAX(id), 0) FROM token_transactions")
            await pg.execute(f"SELECT setval('token_transactions_id_seq', {max_id})")

            # Job status
            rows = conn_sq.execute("SELECT * FROM job_status").fetchall()
            print(f"  job_status: {len(rows)}")
            for r in rows:
                d = dict(r)
                try:
                    await pg.execute("""
                        INSERT INTO job_status (id, kind, status, message, result, repo_id, created_at, updated_at)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING
                    """, d["id"], d["kind"], d["status"], d.get("message"),
                        d.get("result"), d.get("repo_id"), d.get("created_at"), d.get("updated_at"))
                except Exception as e:
                    print(f"    skip job: {e}")

            # Public overviews
            try:
                rows = conn_sq.execute("SELECT * FROM public_overviews").fetchall()
                print(f"  public_overviews: {len(rows)}")
                for r in rows:
                    d = dict(r)
                    try:
                        await pg.execute("""
                            INSERT INTO public_overviews (id, owner, repo_name, repo_url, overview, description,
                                languages, stars, file_count, depth, expertise, created_at, updated_at)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) ON CONFLICT DO NOTHING
                        """, d["id"], d["owner"], d["repo_name"], d["repo_url"], d["overview"],
                            d.get("description"), d.get("languages"), d.get("stars", 0),
                            d.get("file_count", 0), d.get("depth", "high-level"),
                            d.get("expertise", "amateur"), d.get("created_at"), d.get("updated_at"))
                    except Exception as e:
                        print(f"    skip overview: {e}")
                max_id = await pg.fetchval("SELECT COALESCE(MAX(id), 0) FROM public_overviews")
                await pg.execute(f"SELECT setval('public_overviews_id_seq', {max_id})")
            except Exception:
                print("  public_overviews: table not found, skipping")

            # Achievements
            try:
                rows = conn_sq.execute("SELECT * FROM user_achievements").fetchall()
                print(f"  user_achievements: {len(rows)}")
                for r in rows:
                    d = dict(r)
                    try:
                        await pg.execute("""
                            INSERT INTO user_achievements (id, user_id, badge, unlocked_at)
                            VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING
                        """, d["id"], d["user_id"], d["badge"], d.get("unlocked_at"))
                    except Exception as e:
                        print(f"    skip achievement: {e}")
                max_id = await pg.fetchval("SELECT COALESCE(MAX(id), 0) FROM user_achievements")
                await pg.execute(f"SELECT setval('user_achievements_id_seq', {max_id})")
            except Exception:
                print("  user_achievements: table not found, skipping")

            # Anonymous usage
            try:
                rows = conn_sq.execute("SELECT * FROM anonymous_usage").fetchall()
                print(f"  anonymous_usage: {len(rows)}")
                for r in rows:
                    d = dict(r)
                    try:
                        await pg.execute("""
                            INSERT INTO anonymous_usage (ip, overviews_generated, last_used)
                            VALUES ($1,$2,$3) ON CONFLICT DO NOTHING
                        """, d["ip"], d.get("overviews_generated", 0), d.get("last_used"))
                    except Exception as e:
                        print(f"    skip anon: {e}")
            except Exception:
                print("  anonymous_usage: table not found, skipping")

            # Email preferences
            try:
                rows = conn_sq.execute("SELECT * FROM email_preferences").fetchall()
                print(f"  email_preferences: {len(rows)}")
                for r in rows:
                    d = dict(r)
                    try:
                        await pg.execute("""
                            INSERT INTO email_preferences (user_id, welcome, generation_ready, weekly_digest, marketing, updated_at)
                            VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING
                        """, d["user_id"], d.get("welcome", 1), d.get("generation_ready", 1),
                            d.get("weekly_digest", 1), d.get("marketing", 1), d.get("updated_at"))
                    except Exception as e:
                        print(f"    skip email pref: {e}")
            except Exception:
                print("  email_preferences: table not found, skipping")

            # Share counts
            try:
                rows = conn_sq.execute("SELECT * FROM share_counts").fetchall()
                print(f"  share_counts: {len(rows)}")
                for r in rows:
                    d = dict(r)
                    try:
                        await pg.execute("""
                            INSERT INTO share_counts (content_id, platform, count, last_shared)
                            VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING
                        """, d["content_id"], d.get("platform"), d.get("count", 0), d.get("last_shared"))
                    except Exception as e:
                        print(f"    skip share: {e}")
            except Exception:
                print("  share_counts: table not found, skipping")

            # API usage
            try:
                rows = conn_sq.execute("SELECT * FROM api_usage").fetchall()
                print(f"  api_usage: {len(rows)}")
                for r in rows:
                    d = dict(r)
                    try:
                        await pg.execute("""
                            INSERT INTO api_usage (id, user_id, api_key, endpoint, tokens_used, created_at)
                            VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING
                        """, d["id"], d["user_id"], d["api_key"], d["endpoint"],
                            d.get("tokens_used", 0), d.get("created_at"))
                    except Exception as e:
                        print(f"    skip api_usage: {e}")
                max_id = await pg.fetchval("SELECT COALESCE(MAX(id), 0) FROM api_usage")
                await pg.execute(f"SELECT setval('api_usage_id_seq', {max_id})")
            except Exception:
                print("  api_usage: table not found, skipping")

        conn_sq.close()
    else:
        print(f"Main DB not found: {MAIN_DB}")

    await db_postgres.close_pool()
    print("\nMigration complete!")


if __name__ == "__main__":
    asyncio.run(migrate())
