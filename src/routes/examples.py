"""
RepoLM — Examples endpoints.
"""

import os
import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import EXAMPLES_DIR

router = APIRouter()


@router.get("/api/examples")
async def get_examples():
    examples = []
    for fname in sorted(os.listdir(EXAMPLES_DIR)):
        if fname.endswith(".json"):
            with open(os.path.join(EXAMPLES_DIR, fname)) as f:
                examples.append(json.load(f))
    return examples


@router.get("/api/examples/{slug}")
async def get_example(slug: str):
    path = os.path.join(EXAMPLES_DIR, f"{slug}.json")
    if not os.path.exists(path):
        return JSONResponse({"error": "Not found"}, 404)
    with open(path) as f:
        return json.load(f)
