"""
RepoLM — SEO routes: public repo pages, sitemap.xml, robots.txt, OG images.
"""

import json
import re
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

import db as database

router = APIRouter()

BASE_URL = "https://repolm.com"


@router.get("/repo/{owner}/{name}", response_class=HTMLResponse)
async def public_repo_page(owner: str, name: str):
    """SEO-optimized public page for a repo overview."""
    overview = database.get_public_overview(owner, name)
    if not overview:
        return HTMLResponse(
            """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Not Found | RepoLM</title>
            <meta http-equiv="refresh" content="3;url=/app"></head>
            <body style="background:#09090b;color:#e5e7eb;font-family:system-ui;text-align:center;padding-top:100px">
            <h1>Overview not generated yet</h1><p>Redirecting to app...</p></body></html>""",
            status_code=404,
        )

    repo_name = overview["repo_name"]
    owner_name = overview["owner"]
    content = overview["overview"]
    desc_raw = re.sub(r'[#*`\[\]()]', '', content)[:200].replace('\n', ' ').strip()
    desc_safe = desc_raw.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
    title = f"{owner_name}/{repo_name} Explained — Architecture & Overview | RepoLM"
    canonical = f"{BASE_URL}/repo/{owner_name}/{repo_name}"
    languages = overview.get("languages") or ""
    file_count = overview.get("file_count") or 0
    content_escaped = json.dumps(content)

    json_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "name": f"{owner_name}/{repo_name} — Code Overview",
        "description": desc_raw,
        "author": {"@type": "Organization", "name": "RepoLM"},
        "url": canonical,
        "dateModified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(overview.get("updated_at", time.time()))),
    })

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{desc_safe}">
<meta name="keywords" content="{repo_name}, {owner_name}, code overview, architecture, github, explained">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc_safe}">
<meta property="og:type" content="article">
<meta property="og:url" content="{canonical}">
<meta property="og:site_name" content="RepoLM">
<meta property="og:image" content="{BASE_URL}/api/og-image/{owner_name}/{repo_name}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc_safe}">
<meta name="twitter:image" content="{BASE_URL}/api/og-image/{owner_name}/{repo_name}">
<script type="application/ld+json">{json_ld}</script>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
body{{background:#09090b;color:#e5e7eb;font-family:system-ui,sans-serif}}
.prose pre{{background:#0d1117;border-radius:8px;padding:12px;overflow-x:auto;font-size:13px}}
.prose h1,.prose h2,.prose h3{{color:#e5e7eb}}.prose p,.prose li{{color:#d1d5db}}
</style>
</head>
<body>
<nav class="max-w-4xl mx-auto px-6 py-5 flex items-center justify-between">
    <a href="/" class="text-xl font-bold"><span class="text-purple-400">Repo</span>LM</a>
    <div class="flex items-center gap-4">
        <a href="/pricing" class="text-sm text-gray-400 hover:text-purple-300">Pricing</a>
        <a href="/app" class="text-sm text-purple-400 hover:text-purple-300">Open App →</a>
    </div>
</nav>

<main class="max-w-4xl mx-auto px-6 py-8">
    <div class="mb-6">
        <h1 class="text-3xl font-bold mb-2"><span class="text-purple-400">{owner_name}</span>/{repo_name}</h1>
        <div class="flex items-center gap-4 text-sm text-gray-500">
            <span>📁 {file_count} files</span>
            <span>{languages}</span>
            <a href="https://github.com/{owner_name}/{repo_name}" target="_blank" class="text-purple-400 hover:text-purple-300">View on GitHub →</a>
        </div>
    </div>

    <div id="content" class="prose prose-invert prose-sm max-w-none mb-12"></div>

    <!-- CTAs -->
    <div class="bg-gray-900/50 border border-gray-800 rounded-2xl p-8 text-center mb-12">
        <h2 class="text-2xl font-bold mb-3">Want more?</h2>
        <p class="text-gray-400 mb-6">Generate podcasts, slides, and chat with the codebase interactively.</p>
        <div class="flex justify-center gap-4 flex-wrap">
            <a href="/app?url=https://github.com/{owner_name}/{repo_name}" class="bg-purple-600 hover:bg-purple-500 text-white font-semibold px-6 py-3 rounded-xl transition">🎙️ Generate Podcast</a>
            <a href="/app?url=https://github.com/{owner_name}/{repo_name}" class="bg-gray-800 hover:bg-gray-700 text-white font-semibold px-6 py-3 rounded-xl border border-gray-700 transition">📊 Generate Slides</a>
        </div>
    </div>

    <!-- Social share buttons -->
    <div class="flex items-center gap-3 mb-8">
        <span class="text-sm text-gray-500">Share:</span>
        <a href="https://twitter.com/intent/tweet?text=Just%20used%20%40RepoLM%20to%20understand%20{owner_name}%2F{repo_name}%20in%20minutes.%20The%20AI%20podcast%20feature%20is%20%F0%9F%94%A5&url={canonical}"
           target="_blank" class="text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 px-4 py-2 rounded-lg transition">🐦 Twitter</a>
        <a href="https://www.linkedin.com/sharing/share-offsite/?url={canonical}"
           target="_blank" class="text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 px-4 py-2 rounded-lg transition">💼 LinkedIn</a>
    </div>
</main>

<footer class="max-w-4xl mx-auto px-6 py-10 border-t border-gray-800 text-center text-gray-600 text-sm">
    <span class="text-purple-400 font-semibold">Repo</span>LM — AI-powered code education
</footer>

<script>
const content = {content_escaped};
document.getElementById('content').innerHTML = marked.parse(content);
document.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
</script>
</body>
</html>""")


@router.get("/sitemap.xml")
async def sitemap():
    """Generate sitemap.xml with all public repo pages."""
    pages = database.list_public_overviews(limit=5000)
    urls = [f"""  <url>
    <loc>{BASE_URL}/repo/{p['owner']}/{p['repo_name']}</loc>
    <lastmod>{time.strftime('%Y-%m-%d', time.gmtime(p.get('updated_at', time.time())))}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.7</priority>
  </url>""" for p in pages]

    # Static pages
    static = [
        ("", "daily", "1.0"),
        ("/app", "daily", "0.9"),
        ("/pricing", "weekly", "0.8"),
        ("/learn", "weekly", "0.7"),
    ]
    for path, freq, prio in static:
        urls.insert(0, f"""  <url>
    <loc>{BASE_URL}{path}</loc>
    <changefreq>{freq}</changefreq>
    <priority>{prio}</priority>
  </url>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""
    return Response(content=xml, media_type="application/xml")


@router.get("/robots.txt")
async def robots():
    return Response(content=f"""User-agent: *
Allow: /
Allow: /repo/
Allow: /pricing
Allow: /learn

Disallow: /api/
Disallow: /auth/
Disallow: /admin

Sitemap: {BASE_URL}/sitemap.xml
""", media_type="text/plain")


@router.get("/api/og-image/{owner}/{name}")
async def og_image(owner: str, name: str):
    """Generate a simple SVG-based OG image for social sharing."""
    overview = database.get_public_overview(owner, name)
    desc = ""
    if overview:
        desc = re.sub(r'[#*`\[\]()]', '', overview["overview"])[:120].replace('\n', ' ').strip()
    desc_safe = desc.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
    owner_safe = owner.replace('&', '&amp;').replace('<', '&lt;')
    name_safe = name.replace('&', '&amp;').replace('<', '&lt;')

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#09090b"/>
  <rect x="0" y="0" width="1200" height="4" fill="#7c3aed"/>
  <text x="80" y="120" font-family="system-ui,sans-serif" font-size="28" fill="#a78bfa" font-weight="700">RepoLM</text>
  <text x="80" y="260" font-family="system-ui,sans-serif" font-size="56" fill="#e5e7eb" font-weight="700">{owner_safe}/</text>
  <text x="80" y="330" font-family="system-ui,sans-serif" font-size="56" fill="#a78bfa" font-weight="700">{name_safe}</text>
  <text x="80" y="420" font-family="system-ui,sans-serif" font-size="22" fill="#9ca3af">{desc_safe}</text>
  <text x="80" y="560" font-family="system-ui,sans-serif" font-size="18" fill="#6b7280">AI-powered code overview · repolm.com</text>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/trending")
async def trending_repos():
    """Get trending repos for the landing page."""
    repos = database.get_trending_repos(days=7, limit=8)
    return repos
