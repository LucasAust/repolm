"""
RepoLM — Repository Ingestion (v3: GitHub API-first with git clone fallback)
Uses GitHub API for tree + parallel raw file fetch. Falls back to git clone for non-GitHub repos.
"""

import os
import re
import logging
import concurrent.futures
from typing import Optional, Callable

import requests

# Re-export everything the old ingest.py exported (routes import from here)
from ingest import (
    RepoFile, RepoData, SkippedSummary,
    should_skip_file, should_skip_dir, is_test_file, is_config_file,
    detect_language, build_import_graph, build_tree, repo_to_text,
    clone_repo, ingest_repo as _clone_ingest,
    MAX_FILE_SIZE, MAX_TOTAL_CHARS, MAX_FILES_TO_WALK, MAX_FILES_FOR_IMPORT_GRAPH,
    PRIORITY_FILES, ENTRY_POINT_FILES, SKIP_DIRS, SKIP_EXTENSIONS,
)

logger = logging.getLogger("repolm.ingest")

# GitHub API settings
GITHUB_API_TIMEOUT = 15
RAW_FETCH_TIMEOUT = 10
MAX_PARALLEL_FETCHES = 50
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

_session = requests.Session()
if GITHUB_TOKEN:
    _session.headers["Authorization"] = f"token {GITHUB_TOKEN}"
_session.headers["Accept"] = "application/vnd.github.v3+json"


def _parse_github_url(url: str) -> Optional[tuple]:
    """Extract (owner, repo) from a GitHub URL. Returns None if not GitHub."""
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/.*)?$', url.strip())
    if m:
        return m.group(1), m.group(2)
    return None


def _api_ingest(owner: str, repo: str, url: str, progress_callback: Optional[Callable] = None) -> RepoData:
    """Ingest via GitHub API: tree endpoint + parallel raw file fetch."""

    if progress_callback:
        progress_callback("cloning", "Fetching repository structure...")

    # Get default branch
    repo_info = _session.get(
        f"https://api.github.com/repos/{owner}/{repo}",
        timeout=GITHUB_API_TIMEOUT
    )
    repo_info.raise_for_status()
    branch = repo_info.json().get("default_branch", "main")

    # Get full tree
    tree_resp = _session.get(
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1",
        timeout=GITHUB_API_TIMEOUT
    )
    tree_resp.raise_for_status()
    tree_data = tree_resp.json()
    all_entries = tree_data.get("tree", [])

    blobs = [e for e in all_entries if e["type"] == "blob"]
    dirs = {os.path.dirname(e["path"]) for e in all_entries if "/" in e["path"]}

    if progress_callback:
        progress_callback("scanning", f"Found {len(blobs)} files, filtering...")

    # Build simple directory tree string from API data
    tree_str = _build_api_tree(all_entries)

    # Filter files (same logic as clone-based ingest)
    eligible = []
    for blob in blobs:
        path = blob["path"]
        size = blob.get("size", 0)
        parts = path.split("/")

        # Skip dirs
        if any(should_skip_dir(p) for p in parts[:-1]):
            continue
        if should_skip_file(path):
            continue
        if size > MAX_FILE_SIZE:
            continue

        basename = os.path.basename(path)
        is_priority = basename in PRIORITY_FILES
        is_entry = basename in ENTRY_POINT_FILES
        is_test = is_test_file(path)

        eligible.append({
            "path": path, "size": size,
            "is_priority": is_priority, "is_entry": is_entry, "is_test": is_test,
        })

        if len(eligible) >= MAX_FILES_TO_WALK:
            break

    if progress_callback:
        progress_callback("processing", f"Analyzing {len(eligible)} files...")

    # Score and sort (same logic as clone ingest but without import graph for speed)
    def file_sort_key(item):
        if item["is_priority"]:
            tier = 0
        elif item["is_entry"]:
            tier = 1
        elif item["is_test"]:
            tier = 4
        else:
            tier = 3
        return (tier, item["size"])

    eligible.sort(key=file_sort_key)

    # Fetch files in parallel (cap to stay within char budget)
    raw_base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
    data = RepoData(name=repo, url=url, local_path="", tree=tree_str)
    skipped_summary = SkippedSummary()
    language_stats = {}

    # Select files to fetch (within budget estimate — be conservative to avoid wasted HTTP calls)
    to_fetch = []
    estimated_chars = 0
    for f in eligible:
        if estimated_chars >= MAX_TOTAL_CHARS:
            skipped_summary.total += 1
            if f["is_test"]:
                skipped_summary.tests += 1
            else:
                skipped_summary.other += 1
            continue
        to_fetch.append(f)
        estimated_chars += f["size"]

    if progress_callback:
        progress_callback("processing", f"Downloading {len(to_fetch)} key files...")

    def fetch_one(file_info):
        path = file_info["path"]
        try:
            r = _session.get(f"{raw_base}/{path}", timeout=RAW_FETCH_TIMEOUT)
            if r.status_code == 200:
                return path, r.text, file_info
        except Exception:
            pass
        return path, None, file_info

    # Parallel fetch
    fetched = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as pool:
        results = pool.map(fetch_one, to_fetch)
        for path, content, info in results:
            if content is None:
                data.skipped_count += 1
                continue
            if data.total_chars + len(content) > MAX_TOTAL_CHARS:
                data.skipped_count += 1
                skipped_summary.total += 1
                skipped_summary.other += 1
                continue

            lang = detect_language(path)
            language_stats[lang] = language_stats.get(lang, 0) + 1

            data.files.append(RepoFile(
                path=path, content=content, size=len(content),
                is_priority=info["is_priority"], is_entry_point=info["is_entry"],
                is_test=info["is_test"], import_score=0.0,
            ))
            data.total_chars += len(content)

    data.skipped_count += skipped_summary.total
    data.skipped_summary = skipped_summary
    data.language_stats = language_stats

    return data


def _build_api_tree(entries, max_depth=4):
    """Build a tree string from GitHub API tree entries."""
    # Group by directory
    from collections import defaultdict
    tree = defaultdict(list)
    for e in entries:
        parts = e["path"].split("/")
        if len(parts) <= max_depth:
            parent = "/".join(parts[:-1]) if len(parts) > 1 else ""
            name = parts[-1]
            is_dir = e["type"] == "tree"
            if not should_skip_dir(name) or not is_dir:
                tree[parent].append((name, is_dir))

    def render(prefix_path="", indent=""):
        items = sorted(tree.get(prefix_path, []), key=lambda x: (not x[1], x[0]))
        lines = []
        for i, (name, is_dir) in enumerate(items):
            connector = "├── " if i < len(items) - 1 else "└── "
            if is_dir:
                if should_skip_dir(name):
                    continue
                lines.append(f"{indent}{connector}{name}/")
                ext = "│   " if i < len(items) - 1 else "    "
                child_path = f"{prefix_path}/{name}" if prefix_path else name
                sub = render(child_path, indent + ext)
                if sub:
                    lines.append(sub)
            else:
                if not should_skip_file(name):
                    lines.append(f"{indent}{connector}{name}")
        return "\n".join(lines)

    return render()


def ingest_repo(url: str, progress_callback: Optional[Callable] = None) -> RepoData:
    """
    Smart ingest: use GitHub API for GitHub repos (fast), fall back to git clone for others.
    """
    parsed = _parse_github_url(url)
    if parsed:
        owner, repo = parsed
        try:
            return _api_ingest(owner, repo, url, progress_callback)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (403, 404):
                # Private repo or rate limited — fall back to clone
                logger.info("GitHub API failed (%s), falling back to clone for %s", e.response.status_code, url)
            else:
                logger.warning("GitHub API error for %s, falling back to clone", url, exc_info=True)
        except Exception:
            logger.warning("GitHub API ingest failed for %s, falling back to clone", url, exc_info=True)

    # Fallback: git clone (works for GitLab, private repos, etc.)
    return _clone_ingest(url, progress_callback)
