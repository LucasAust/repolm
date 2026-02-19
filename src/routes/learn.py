"""
RepoLM — Learning paths endpoints.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from learning_paths import get_all_paths, get_path_by_id

router = APIRouter()

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


@router.get("/api/learning-paths")
async def api_learning_paths():
    return get_all_paths()


@router.get("/api/learning-paths/{path_id}")
async def api_learning_path(path_id: str):
    path = get_path_by_id(path_id)
    if not path:
        return JSONResponse({"error": "Not found"}, 404)
    return path


@router.get("/learn", response_class=HTMLResponse)
async def learn_page():
    return HTMLResponse(TEMPLATES_DIR.joinpath("learn.html").read_text())
