"""
RepoLM — Uvicorn entry point (development).
For production, use gunicorn (see Dockerfile).
"""

import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    reload = os.environ.get("REPOLM_ENV", "development") == "development"
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=reload)
