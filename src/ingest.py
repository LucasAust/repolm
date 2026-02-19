"""
RepoLM — Repository Ingestion (v2: Smart file scoring + dependency graph)
Clones a GitHub repo and extracts a smart, LLM-friendly text representation.
"""

import os
import re
import sys
import shutil
import subprocess
import fnmatch
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

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

# Entry-point files get extra priority
ENTRY_POINT_FILES = [
    "main.py", "app.py", "server.py", "index.py", "cli.py", "run.py", "__main__.py",
    "index.js", "index.ts", "index.tsx", "app.js", "app.ts", "app.tsx",
    "server.js", "server.ts", "main.js", "main.ts", "main.go", "main.rs",
    "lib.rs", "mod.rs", "cmd/main.go",
    "manage.py", "wsgi.py", "asgi.py",
]

# Test file patterns (deprioritize)
TEST_PATTERNS = [
    "*test*", "*spec*", "*_test.*", "*_spec.*", "test_*", "spec_*",
    "tests/*", "test/*", "__tests__/*", "spec/*", "specs/*",
    "*fixture*", "*mock*", "conftest.py",
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
    is_entry_point: bool = False
    is_test: bool = False
    import_score: float = 0.0  # how many other files import this


@dataclass
class SkippedSummary:
    total: int = 0
    tests: int = 0
    configs: int = 0
    utilities: int = 0
    large_files: int = 0
    other: int = 0

    def to_string(self):
        if self.total == 0:
            return ""
        parts = []
        if self.tests:
            parts.append("{} tests".format(self.tests))
        if self.configs:
            parts.append("{} config files".format(self.configs))
        if self.large_files:
            parts.append("{} large files".format(self.large_files))
        if self.utilities:
            parts.append("{} utilities".format(self.utilities))
        if self.other:
            parts.append("{} other".format(self.other))
        return "Also includes {} more files not shown: {}".format(self.total, ", ".join(parts))


@dataclass
class RepoData:
    name: str
    url: str
    local_path: str
    tree: str  # directory tree string
    files: List[RepoFile] = field(default_factory=list)
    total_chars: int = 0
    skipped_count: int = 0
    skipped_summary: Optional[SkippedSummary] = None
    language_stats: dict = field(default_factory=dict)


import tempfile
import logging

_clone_logger = logging.getLogger("repolm.ingest")

MAX_REPO_SIZE_MB = 500


def _check_repo_size(url: str) -> bool:
    """Check if repo is within size limit. Returns True if OK."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--get-url", url],
            capture_output=True, text=True, timeout=15,
        )
        # Can't reliably get size from ls-remote, so we skip oversized repos
        # during clone by using --depth 1
        return True
    except Exception:
        return True  # allow on check failure


def clone_repo(url: str, dest: Optional[str] = None) -> str:
    """Clone a GitHub repo into a temp directory. Returns local path."""
    if dest is None:
        dest = tempfile.mkdtemp(prefix="repolm_")

    _clone_logger.info("Cloning %s -> %s", url, dest)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", url, dest],
            check=True, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError("Git clone timed out after 60 seconds")
    except subprocess.CalledProcessError as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError("Git clone failed: {}".format(e.stderr.strip() if e.stderr else str(e)))
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


def is_test_file(rel_path: str) -> bool:
    """Check if a file is a test file based on path patterns."""
    lower = rel_path.lower()
    parts = lower.split("/")
    # Directory-based test detection
    for part in parts[:-1]:
        if part in ("tests", "test", "__tests__", "spec", "specs", "testing"):
            return True
    # File-name based
    basename = parts[-1]
    if basename.startswith("test_") or basename.startswith("spec_"):
        return True
    name_no_ext = os.path.splitext(basename)[0]
    if name_no_ext.endswith("_test") or name_no_ext.endswith("_spec") or name_no_ext.endswith(".test") or name_no_ext.endswith(".spec"):
        return True
    if "test" in basename and basename != "testutils.py":
        # Be more careful: only flag if 'test' is a clear boundary
        if re.search(r'(?:^|[_.\-])test(?:[_.\-s]|$)', basename):
            return True
    if basename == "conftest.py":
        return True
    return False


def is_config_file(rel_path: str) -> bool:
    """Check if a file is a config/meta file."""
    basename = os.path.basename(rel_path).lower()
    config_names = {
        ".eslintrc", ".prettierrc", ".babelrc", "tsconfig.json", "jest.config",
        ".editorconfig", ".gitignore", ".dockerignore", "tox.ini", "mypy.ini",
        ".flake8", ".pylintrc", "setup.cfg", ".pre-commit-config.yaml",
        "renovate.json", ".dependabot", "codecov.yml",
    }
    for cn in config_names:
        if basename.startswith(cn):
            return True
    if basename.startswith(".") and not basename.startswith(".."):
        return True
    return False


def build_import_graph(file_paths: List[str], root: str) -> Dict[str, float]:
    """
    Scan Python and JS/TS files for imports. Return a dict of rel_path -> import_score
    (how many other files reference this module).
    """
    # Map module names to file paths
    module_to_path = {}  # type: Dict[str, str]
    for rel_path in file_paths:
        ext = os.path.splitext(rel_path)[1].lower()
        # Python: foo/bar.py -> foo.bar, foo/__init__.py -> foo
        if ext == ".py":
            mod = rel_path.replace("/", ".").replace("\\", ".")
            if mod.endswith(".__init__.py"):
                mod = mod[:-12]
            elif mod.endswith(".py"):
                mod = mod[:-3]
            module_to_path[mod] = rel_path
            # Also map just the filename without extension
            basename = os.path.splitext(os.path.basename(rel_path))[0]
            if basename != "__init__":
                module_to_path.setdefault(basename, rel_path)
        elif ext in (".js", ".ts", ".tsx", ".jsx", ".mjs"):
            # JS: foo/bar.js -> foo/bar, also just bar
            no_ext = rel_path
            for suffix in (".js", ".ts", ".tsx", ".jsx", ".mjs", "/index.js", "/index.ts", "/index.tsx"):
                if no_ext.endswith(suffix):
                    no_ext = no_ext[:-len(suffix)]
                    break
            module_to_path[no_ext] = rel_path
            basename = os.path.basename(no_ext)
            module_to_path.setdefault(basename, rel_path)

    # Count imports
    import_counts = defaultdict(int)  # type: Dict[str, int]

    for rel_path in file_paths:
        ext = os.path.splitext(rel_path)[1].lower()
        fpath = os.path.join(root, rel_path)
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(50000)  # only scan first 50k
        except Exception:
            continue

        imported_modules = set()

        if ext == ".py":
            # from foo.bar import X
            for m in re.finditer(r'^\s*from\s+([\w.]+)\s+import', content, re.MULTILINE):
                imported_modules.add(m.group(1))
            # import foo, import foo.bar
            for m in re.finditer(r'^\s*import\s+([\w.]+)', content, re.MULTILINE):
                imported_modules.add(m.group(1))
        elif ext in (".js", ".ts", ".tsx", ".jsx", ".mjs"):
            # import X from './foo' / require('./foo')
            for m in re.finditer(r"""(?:import\s.*?from\s+|require\s*\(\s*)['"]([^'"]+)['"]""", content):
                mod = m.group(1)
                if mod.startswith("."):
                    # Resolve relative import
                    dir_of_file = os.path.dirname(rel_path)
                    resolved = os.path.normpath(os.path.join(dir_of_file, mod))
                    imported_modules.add(resolved)
                else:
                    imported_modules.add(mod)

        # Match imported modules to known files
        for mod in imported_modules:
            # Try direct match
            target = module_to_path.get(mod)
            if target and target != rel_path:
                import_counts[target] += 1
                continue
            # Try partial match (last component)
            parts = mod.replace("\\", "/").split("/")
            last = parts[-1].split(".")[-1]
            target = module_to_path.get(last)
            if target and target != rel_path:
                import_counts[target] += 1

    # Normalize to 0-1 range
    if not import_counts:
        return {}
    max_count = max(import_counts.values())
    if max_count == 0:
        return {}
    return {path: count / max_count for path, count in import_counts.items()}


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
            lines.append("{}{}{}/ ".format(prefix, connector, name))
            extension = "│   " if i < len(items) - 1 else "    "
            subtree = build_tree(os.path.join(root, name), prefix + extension, max_depth, depth + 1)
            if subtree:
                lines.append(subtree)
        else:
            lines.append("{}{}{}".format(prefix, connector, name))

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


def _classify_skipped(rel_path: str, size: int) -> str:
    """Classify a skipped file for the summary."""
    if is_test_file(rel_path):
        return "test"
    if is_config_file(rel_path):
        return "config"
    if size > MAX_FILE_SIZE:
        return "large"
    return "other"


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
    all_files = []  # type: List[Tuple[str, str, int, bool, bool, bool]]
    all_rel_paths = []

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
            is_entry = os.path.basename(fpath) in ENTRY_POINT_FILES
            is_test = is_test_file(rel_path)
            all_files.append((rel_path, fpath, size, is_priority, is_entry, is_test))
            all_rel_paths.append(rel_path)

    # Build import graph for scoring
    import_scores = build_import_graph(all_rel_paths, local_path)

    # Score each file: lower score = included first
    def file_sort_key(item):
        rel_path, fpath, size, is_priority, is_entry, is_test_file = item
        imp_score = import_scores.get(rel_path, 0.0)

        # Priority: 0 = highest priority, 5 = lowest
        if is_priority:
            tier = 0
        elif is_entry:
            tier = 1
        elif imp_score > 0.5:
            tier = 2  # highly imported
        elif is_test_file:
            tier = 4  # tests last
        elif imp_score > 0.1:
            tier = 2.5
        else:
            tier = 3

        # Within tier, prefer smaller files (more diverse coverage)
        return (tier, -imp_score, size)

    all_files.sort(key=file_sort_key)

    # Track what we skip for summary
    skipped_summary = SkippedSummary()

    # Read files within budget
    for rel_path, fpath, size, is_priority, is_entry, is_test in all_files:
        if data.total_chars >= MAX_TOTAL_CHARS:
            skipped_summary.total += 1
            cat = _classify_skipped(rel_path, size)
            if cat == "test":
                skipped_summary.tests += 1
            elif cat == "config":
                skipped_summary.configs += 1
            elif cat == "large":
                skipped_summary.large_files += 1
            else:
                skipped_summary.other += 1
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
            is_entry_point=is_entry,
            is_test=is_test,
            import_score=import_scores.get(rel_path, 0.0),
        ))
        data.total_chars += len(content)

    data.skipped_summary = skipped_summary

    # Cleanup cloned repo from disk
    try:
        shutil.rmtree(local_path, ignore_errors=True)
        _clone_logger.info("Cleaned up clone dir: %s", local_path)
    except Exception:
        pass

    return data


def repo_to_text(data: RepoData) -> str:
    """Convert RepoData to a single text document for LLM consumption."""
    sections = []

    sections.append("# Repository: {}".format(data.name))
    sections.append("URL: {}".format(data.url))
    sections.append("Files included: {} ({} skipped)".format(len(data.files), data.skipped_count))

    # Language breakdown
    if data.language_stats:
        top_langs = sorted(data.language_stats.items(), key=lambda x: -x[1])[:8]
        lang_str = ", ".join("{}: {}".format(lang, count) for lang, count in top_langs)
        sections.append("Languages: {}".format(lang_str))

    sections.append("\n## Directory Structure\n```\n{}\n```".format(data.tree))

    # Priority files first, then entry points, then by import score
    priority = [f for f in data.files if f.is_priority]
    entry_points = [f for f in data.files if f.is_entry_point and not f.is_priority]
    regular = [f for f in data.files if not f.is_priority and not f.is_entry_point]

    for f in priority + entry_points + regular:
        ext = os.path.splitext(f.path)[1].lstrip(".")
        sections.append("\n## {}\n```{}\n{}\n```".format(f.path, ext, f.content))

    # Add skipped files summary
    if data.skipped_summary and data.skipped_summary.total > 0:
        sections.append("\n---\n{}".format(data.skipped_summary.to_string()))

    return "\n".join(sections)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ingest.py <github_url>")
        sys.exit(1)

    url = sys.argv[1]
    data = ingest_repo(url)
    text = repo_to_text(data)

    out_path = "output/{}_raw.txt".format(data.name)
    os.makedirs("output", exist_ok=True)
    with open(out_path, "w") as f:
        f.write(text)

    print("\nIngested {}:".format(data.name))
    print("  Files: {} ({} skipped)".format(len(data.files), data.skipped_count))
    print("  Total chars: {:,}".format(data.total_chars))
    if data.skipped_summary and data.skipped_summary.total > 0:
        print("  {}".format(data.skipped_summary.to_string()))
    print("  Output: {}".format(out_path))
