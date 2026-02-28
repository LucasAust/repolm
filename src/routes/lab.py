"""
RepoLM — Concept Lab endpoints.
"""

import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from config import TOKEN_COSTS
from auth import get_current_user
import db as database
import state
from services.concept_gen import generate_concept_repo_stream, parse_generated_repo
from routes._helpers import sse_format
import asyncio
import queue as _queue

router = APIRouter()


@router.post("/api/concept-lab")
async def concept_lab(request: Request):
    """Generate a teaching repo from a concept description. Costs 50 tokens."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Sign in to use Concept Lab"}, 401)
    cost = TOKEN_COSTS["concept_lab"]
    balance = database.get_token_balance(user["id"])
    if balance < cost:
        return JSONResponse({"error": "insufficient_tokens", "required": cost, "balance": balance}, 402)

    body = await request.json()
    concept = body.get("concept", "").strip()
    language = body.get("language", "Python")
    difficulty = body.get("difficulty", "intermediate")
    if not concept:
        return JSONResponse({"error": "Concept description required"}, 400)

    database.spend_tokens(user["id"], cost, f"Concept Lab: {concept[:50]}")

    async def event_stream():
        full_text = ""
        try:
            _SENTINEL = object()
            q: _queue.Queue = _queue.Queue(maxsize=64)

            def _producer():
                try:
                    for chunk in generate_concept_repo_stream(concept, language, difficulty):
                        q.put(chunk)
                    q.put(_SENTINEL)
                except Exception as exc:
                    q.put(exc)

            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, _producer)

            while True:
                while True:
                    try:
                        item = q.get_nowait()
                        break
                    except _queue.Empty:
                        await asyncio.sleep(0.01)
                        continue
                if item is _SENTINEL:
                    break
                if isinstance(item, Exception):
                    raise item
                full_text += item
                yield sse_format(item, "chunk")
            repo = parse_generated_repo(full_text)
            if repo:
                repo_id = str(uuid.uuid4())[:8]
                state.repos.set(repo_id, {
                    "status": "ready", "message": "Ready",
                    "data": repo["data"], "files": repo["files"], "text": repo["text"],
                })
                yield sse_format(json.dumps({"repo_id": repo_id, "name": repo["name"], "file_count": len(repo["files"])}), "repo_ready")
            else:
                yield sse_format("Failed to parse generated code. Try again.", "error")
            yield sse_format("", "done")
        except Exception as e:
            yield sse_format(str(e), "error")

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
