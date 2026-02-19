"""
RepoLM — Concept Lab generation service.
Re-exports from concept_lab.py for backward compatibility.
"""

from concept_lab import generate_concept_repo_stream, parse_generated_repo

__all__ = ["generate_concept_repo_stream", "parse_generated_repo"]
