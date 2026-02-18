"""Pre-generate example content for landing page."""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(__file__))
from ingest import ingest_repo, repo_to_text
from summarize import call_llm

EXAMPLES = [
    {"repo": "expressjs/express", "slug": "express", "name": "Express.js", "description": "Fast, unopinionated web framework for Node.js", "tags": ["JavaScript", "Web Framework", "Node.js"]},
    {"repo": "pallets/flask", "slug": "flask", "name": "Flask", "description": "Lightweight Python web framework", "tags": ["Python", "Web Framework", "WSGI"]},
]

OVERVIEW_SYSTEM = """You are RepoLM, an expert code educator. Analyze GitHub repositories and explain them clearly.
Focus on architecture, design decisions, and the big picture. Skip implementation details.
Explain for someone who codes regularly but may not know this domain well.
Be thorough but engaging. Use examples from the actual code."""

PODCAST_SYSTEM = """You are a scriptwriter for a technical podcast called "RepoLM".
Two hosts — Alex (enthusiastic, asks great questions) and Sam (deep technical knowledge, amazing analogies) — break down a codebase.
Focus on architecture, design decisions, and the big picture.
Explain for someone who codes regularly but may not know this domain well.
Write a natural, engaging conversation (2000-3000 words, ~12 min).
Format: ALEX: [text]  /  SAM: [text]"""

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "examples")
os.makedirs(OUTPUT_DIR, exist_ok=True)

for ex in EXAMPLES:
    out_path = os.path.join(OUTPUT_DIR, f"{ex['slug']}.json")
    if os.path.exists(out_path):
        print(f"Skipping {ex['repo']} (already exists)")
        continue
    
    print(f"\n=== {ex['repo']} ===")
    print("Ingesting...")
    url = f"https://github.com/{ex['repo']}"
    data = ingest_repo(url)
    text = repo_to_text(data)
    if len(text) > 200_000:
        text = text[:200_000] + "\n\n[... truncated ...]"
    
    print("Generating overview...")
    overview = call_llm(OVERVIEW_SYSTEM, f"Analyze this repository:\n\n{text}")
    
    print("Generating podcast...")
    podcast = call_llm(PODCAST_SYSTEM, f"Write a podcast script about this repository:\n\n{text}")
    
    result = {
        "slug": ex["slug"],
        "repo": ex["repo"],
        "name": ex["name"],
        "description": ex["description"],
        "tags": ex["tags"],
        "depth": "high-level",
        "expertise": "amateur",
        "overview": overview,
        "podcast": podcast,
    }
    
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Saved: {out_path}")

print("\nDone!")
