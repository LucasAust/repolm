"""
RepoLM — Podcast audio endpoints. Uses thread pool for audio generation.
"""

import json
import logging
import os
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, FileResponse

from config import TOKEN_COSTS
from auth import get_current_user
import db_async
import state
from concurrency import audio_queue
from services.audio_gen import generate_podcast_audio

router = APIRouter()
logger = logging.getLogger("repolm")


def run_audio_gen(audio_id, script_text):
    """Background worker: generate podcast audio. Runs in thread pool (sync is fine)."""
    db_async.sync_update_job(audio_id, status="generating")
    try:
        path = generate_podcast_audio(script_text, audio_id)
        db_async.sync_update_job(audio_id, status="done", result=path)
        state.audio_jobs.set(audio_id, {"status": "done", "path": path})
    except Exception as e:
        logger.exception("Audio gen failed for %s", audio_id)
        db_async.sync_update_job(audio_id, status="error", message=str(e))


@router.post("/api/podcast-audio")
async def podcast_audio(request: Request):
    user = await get_current_user(request)
    cost = TOKEN_COSTS["audio"]
    if user:
        balance = await db_async.get_token_balance(user["id"])
        if balance < cost:
            return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)
        await db_async.spend_tokens(user["id"], cost, "Podcast audio generation")
    body = await request.json()
    script = body.get("script", "")
    if not script:
        return JSONResponse({"error": "Script required"}, 400)
    audio_id = str(uuid.uuid4())[:8]
    await db_async.create_job(audio_id, kind="audio", status="queued", message="")
    state.audio_jobs.set(audio_id, {"status": "queued", "path": None, "message": "", "progress": None, "total": 0, "started_at": None})

    status, queue_pos = audio_queue.submit(audio_id, run_audio_gen, audio_id, script)
    if status == "rejected":
        return JSONResponse({"error": "Server busy, try again in a moment"}, 503)

    result = {"audio_id": audio_id}
    if status == "queued":
        result["queued"] = True
        result["queue_position"] = queue_pos
    return result


@router.get("/api/podcast-audio/{audio_id}")
async def get_podcast_audio(audio_id: str):
    mem_job = state.audio_jobs.get(audio_id)
    if mem_job and mem_job["status"] == "done" and mem_job.get("path"):
        return {"status": "done", "url": "/api/podcast-audio/{}/file".format(audio_id)}

    job = await db_async.get_job(audio_id)
    if not job:
        return JSONResponse({"error": "Not found"}, 404)
    if job["status"] == "done" and job.get("result"):
        return {"status": "done", "url": "/api/podcast-audio/{}/file".format(audio_id)}
    result = {"status": job["status"], "message": job.get("message", "")}

    pos = audio_queue.get_position(audio_id)
    if pos is not None:
        result["queue_position"] = pos

    if mem_job and mem_job.get("progress") is not None:
        result["progress"] = mem_job["progress"]
        result["total"] = mem_job.get("total", 0)
        started = mem_job.get("started_at")
        if started and mem_job["progress"] > 0:
            elapsed = time.time() - started
            rate = mem_job["progress"] / elapsed
            remaining = (mem_job["total"] - mem_job["progress"]) / rate if rate > 0 else 0
            result["eta_seconds"] = round(remaining)
    return result


@router.get("/api/podcast-audio/{audio_id}/file")
async def get_podcast_audio_file(audio_id: str):
    mem_job = state.audio_jobs.get(audio_id)
    if mem_job and mem_job.get("path"):
        return FileResponse(mem_job["path"], media_type="audio/mpeg", filename="podcast.mp3")
    job = await db_async.get_job(audio_id)
    if job and job.get("result"):
        return FileResponse(job["result"], media_type="audio/mpeg", filename="podcast.mp3")
    return JSONResponse({"error": "Not found"}, 404)
