"""
RepoLM — Repository Ingestion
Clones a GitHub repo and extracts a smart, LLM-friendly text representation.
"""

import os
import sys
import shutil
import subprocess
import fnmatch
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

# Max file size to include (skip binaries / huge files)
MAX_FILE_SIZE = 100_000  # 100KB
MAX_TOTAL_CHARS = 500_000  # 500K chars total budget

# Files to always include if present (high signal)
PRIORITY_FILES = [
    "README.md", "README.rst", "README.txt", "README",
    "ARCHITECTURE.md", "CONTRIBUTING.md", "DESIGN.md",
    "package.json", "Cargo.toml", "pyproject.toml", "setup.py", "setup.cfg",
    "go.mod", "Gemfile", "pom.xml", "build.gradle",
    "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example", "requirements.txt",
]

# Directories to skip entirely
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "target", "vendor",
    ".tox", ".mypy_cache", ".pytest_cache", "coverage",
    ".idea", ".vscode", ".DS_Store", "eggs", "*.egg-info",
}

# Extensions to skip (binary / non-useful)
SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".zip", ".tar", ".gz", ".bz2", ".rar", ".7z",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pyc", ".pyo", ".so", ".dll", ".dylib",
    ".exe", ".bin", ".dat", ".db", ".sqlite",
    ".lock", ".sum",  # lock files are noise
    ".min.js", ".min.css",  # minified
}


@dataclass
class RepoFile:
    path: str  # relative to repo root
    content: str
    size: int
    is_priority: bool = False


@dataclass
class RepoData:
    name: str
    url: str
    local_path: str
    tree: str  # directory tree string
    files: List[RepoFile] = field(default_factory=list)
    total_chars: int = 0
    skipped_count: int = 0
    language_stats: dict = field(default_factory=dict)


def clone_repo(url: str, dest: Optional[str] = None) -> str:
    """Clone a GitHub repo. Returns local path."""
    if dest is None:
        repo_name = url.rstrip("/").split("/")[-1].replace(".git", "")
        dest = os.path.join("/tmp/repolm_repos", repo_name)

    if os.path.exists(dest):
        shutil.rmtree(dest)

    print(f"Cloning {url}...")
    subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", url, dest],
        check=True, capture_output=True, text=True,
    )
    return dest


def should_skip_dir(dirname: str) -> bool:
    return dirname in SKIP_DIRS or dirname.startswith(".")


def should_skip_file(filepath: str) -> bool:
    name = os.path.basename(filepath)
    _, ext = os.path.splitext(name)
    if ext.lower() in SKIP_EXTENSIONS:
        return True
    if name.endswith(".min.js") or name.endswith(".min.css"):
        return True
    return False


def build_tree(root: str, prefix: str = "", max_depth: int = 4, depth: int = 0) -> str:
    """Build a directory tree string."""
    if depth >= max_depth:
        return ""

    lines = []
    entries = sorted(os.listdir(root))
    dirs = [e for e in entries if os.path.isdir(os.path.join(root, e)) and not should_skip_dir(e)]
    files = [e for e in entries if os.path.isfile(os.path.join(root, e)) and not should_skip_file(os.path.join(root, e))]

    items = [(d, True) for d in dirs] + [(f, False) for f in files]

    for i, (name, is_dir) in enumerate(items):
        connector = "├── " if i < len(items) - 1 else "└── "
        if is_dir:
            lines.append(f"{prefix}{connector}{name}/")
            extension = "│   " if i < len(items) - 1 else "    "
            subtree = build_tree(os.path.join(root, name), prefix + extension, max_depth, depth + 1)
            if subtree:
                lines.append(subtree)
        else:
            lines.append(f"{prefix}{connector}{name}")

    return "\n".join(lines)


def detect_language(filepath: str) -> str:
    ext_map = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".tsx": "TypeScript", ".jsx": "JavaScript", ".rb": "Ruby",
        ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
        ".c": "C", ".cpp": "C++", ".h": "C/C++", ".cs": "C#",
        ".swift": "Swift", ".php": "PHP", ".lua": "Lua",
        ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
        ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
        ".sql": "SQL", ".md": "Markdown", ".yml": "YAML", ".yaml": "YAML",
        ".json": "JSON", ".toml": "TOML", ".xml": "XML",
    }
    _, ext = os.path.splitext(filepath)
    return ext_map.get(ext.lower(), "Other")


def ingest_repo(url: str) -> RepoData:
    """Clone and ingest a GitHub repo into structured data."""
    local_path = clone_repo(url)
    repo_name = os.path.basename(local_path)

    data = RepoData(
        name=repo_name,
        url=url,
        local_path=local_path,
        tree=build_tree(local_path),
    )

    # Collect all eligible files
    all_files = []
    for root, dirs, files in os.walk(local_path):
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]
        for fname in files:
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, local_path)
            if should_skip_file(fpath):
                data.skipped_count += 1
                continue
            try:
                size = os.path.getsize(fpath)
            except OSError:
                continue
            if size > MAX_FILE_SIZE:
                data.skipped_count += 1
                continue
            is_priority = os.path.basename(fpath) in PRIORITY_FILES
            all_files.append((rel_path, fpath, size, is_priority))

    # Sort: priority files first, then by size (smaller first = more files)
    all_files.sort(key=lambda x: (not x[3], x[2]))

    # Read files within budget
    for rel_path, fpath, size, is_priority in all_files:
        if data.total_chars >= MAX_TOTAL_CHARS:
            data.skipped_count += 1
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            data.skipped_count += 1
            continue

        lang = detect_language(fpath)
        data.language_stats[lang] = data.language_stats.get(lang, 0) + 1

        data.files.append(RepoFile(
            path=rel_path,
            content=content,
            size=len(content),
            is_priority=is_priority,
        ))
        data.total_chars += len(content)

    return data


def repo_to_text(data: RepoData) -> str:
    """Convert RepoData to a single text document for LLM consumption."""
    sections = []

    sections.append(f"# Repository: {data.name}")
    sections.append(f"URL: {data.url}")
    sections.append(f"Files included: {len(data.files)} ({data.skipped_count} skipped)")

    # Language breakdown
    if data.language_stats:
        top_langs = sorted(data.language_stats.items(), key=lambda x: -x[1])[:8]
        lang_str = ", ".join(f"{lang}: {count}" for lang, count in top_langs)
        sections.append(f"Languages: {lang_str}")

    sections.append(f"\n## Directory Structure\n```\n{data.tree}\n```")

    # Priority files first
    priority = [f for f in data.files if f.is_priority]
    regular = [f for f in data.files if not f.is_priority]

    for f in priority + regular:
        ext = os.path.splitext(f.path)[1].lstrip(".")
        sections.append(f"\n## {f.path}\n```{ext}\n{f.content}\n```")

    return "\n".join(sections)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ingest.py <github_url>")
        sys.exit(1)

    url = sys.argv[1]
    data = ingest_repo(url)
    text = repo_to_text(data)

    out_path = f"output/{data.name}_raw.txt"
    os.makedirs("output", exist_ok=True)
    with open(out_path, "w") as f:
        f.write(text)

    print(f"\nIngested {data.name}:")
    print(f"  Files: {len(data.files)} ({data.skipped_count} skipped)")
    print(f"  Total chars: {data.total_chars:,}")
    print(f"  Output: {out_path}")
