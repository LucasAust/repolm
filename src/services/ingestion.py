"""
RepoLM — Ingestion service: repo cloning, file scoring, import graph.
Re-exports from ingest.py for backward compatibility.
"""

from ingest import (
    ingest_repo, repo_to_text, RepoData, RepoFile,
    should_skip_file, should_skip_dir, build_tree,
    detect_language, MAX_FILE_SIZE, MAX_TOTAL_CHARS, PRIORITY_FILES,
    MAX_FILES_TO_WALK, MAX_FILES_FOR_IMPORT_GRAPH,
)

__all__ = [
    "ingest_repo", "repo_to_text", "RepoData", "RepoFile",
    "should_skip_file", "should_skip_dir", "build_tree",
    "detect_language", "MAX_FILE_SIZE", "MAX_TOTAL_CHARS", "PRIORITY_FILES",
    "MAX_FILES_TO_WALK", "MAX_FILES_FOR_IMPORT_GRAPH",
]
