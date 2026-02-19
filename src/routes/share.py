"""
RepoLM — Share & export endpoints.
"""

import io
import re
import json
import time
import zipfile

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import Response

import state

router = APIRouter()


@router.post("/api/share")
async def create_share(request: Request):
    """Create a shareable URL for generated content."""
    import uuid
    body = await request.json()
    content = body.get("content", "")
    kind = body.get("kind", "overview")
    repo_name = body.get("repo_name", "repo")
    if not content:
        return JSONResponse({"error": "No content to share"}, 400)
    short_id = str(uuid.uuid4())[:8]
    state.shared_content.set(short_id, {
        "kind": kind, "content": content,
        "repo_name": repo_name, "created_at": time.time(),
    })
    return {"share_id": short_id, "url": f"/share/{short_id}"}


@router.get("/share/{share_id}", response_class=HTMLResponse)
async def view_shared(share_id: str):
    """View shared content with full SEO."""
    item = state.shared_content.get(share_id)
    if not item:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    kind = item["kind"]
    repo_name = item["repo_name"]
    content_raw = item["content"]
    content_escaped = json.dumps(content_raw)
    desc_text = re.sub(r'[#*`\[\]()]', '', content_raw)[:200].replace('\n', ' ').strip()
    if len(content_raw) > 200:
        desc_text += "..."
    desc_safe = desc_text.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
    title_safe = f"{repo_name} - {kind.title()} | RepoLM"
    preview_text = re.sub(r'[#*`\[\]()]', '', content_raw)[:500].replace('\n', '<br>').replace('<', '&lt;').replace('>', '&gt;')
    kind_map = {"overview": "TechArticle", "podcast": "PodcastEpisode", "slides": "PresentationDigitalDocument"}
    schema_type = kind_map.get(kind, "Article")
    json_ld = json.dumps({
        "@context": "https://schema.org", "@type": schema_type,
        "name": title_safe, "description": desc_text,
        "author": {"@type": "Organization", "name": "RepoLM"},
        "datePublished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(item.get("created_at", time.time()))),
    })

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title_safe}</title>
<meta name="description" content="{desc_safe}">
<meta property="og:title" content="{title_safe}">
<meta property="og:description" content="{desc_safe}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="RepoLM">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title_safe}">
<meta name="twitter:description" content="{desc_safe}">
<script type="application/ld+json">{json_ld}</script>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>body{{background:#09090b;color:#e5e7eb;font-family:system-ui,sans-serif}}
.prose pre{{background:#0d1117;border-radius:8px;padding:12px;overflow-x:auto;font-size:13px}}
.prose h1,.prose h2,.prose h3{{color:#e5e7eb}}.prose p,.prose li{{color:#d1d5db}}</style>
</head><body><div class="max-w-4xl mx-auto px-6 py-8">
<div class="flex items-center justify-between mb-6">
<div><a href="/" class="text-xl font-bold"><span class="text-purple-400">Repo</span>LM</a>
<span class="text-gray-500 ml-3">{repo_name} · {kind.title()}</span></div>
<a href="/app" class="text-sm text-purple-400 hover:text-purple-300">Open App →</a></div>
<noscript><div class="prose prose-invert prose-sm max-w-none"><p>{preview_text}</p></div></noscript>
<div id="content" class="prose prose-invert prose-sm max-w-none"></div>
</div><script>
const content = {content_escaped};
document.getElementById('content').innerHTML = marked.parse(content);
document.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
</script></body></html>""")


@router.post("/api/export-all")
async def export_all(request: Request):
    """Bundle overview + podcast + slides into a zip download."""
    body = await request.json()
    repo_name = body.get("repo_name", "repo")
    items = body.get("items", [])
    if not items:
        return JSONResponse({"error": "Nothing to export"}, 400)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            kind = item.get("kind", "content")
            content = item.get("content", "")
            fname = f"{repo_name}_{kind}.md"
            zf.writestr(fname, content)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{repo_name}_repolm.zip"'}
    )
