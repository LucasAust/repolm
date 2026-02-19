"""
RepoLM — Concept Lab
Generate small teaching repos from concept descriptions using LLM.
"""

import json
import re
from typing import Optional
from summarize import call_llm_stream

CONCEPT_LAB_SYSTEM = """You are RepoLM Concept Lab, an expert developer and educator.
The user wants to learn a concept. Generate a small but COMPLETE codebase (3-8 files) that demonstrates it.

Output a JSON object with this exact structure:
{
  "name": "concept-demo",
  "files": [
    {"path": "README.md", "content": "# Concept Demo\\n..."},
    {"path": "main.py", "content": "..."},
    ...
  ]
}

Rules:
1. README.md MUST be the first file — explain the concept, how the code works, and how to run it
2. All source files MUST have extensive comments explaining what each part does and WHY
3. Include a main entry point that can be run directly
4. Include example usage or a demo script
5. Keep it focused — teach ONE concept well, not everything
6. Use real, working code — no placeholders or TODOs
7. Match the requested difficulty level:
   - beginner: Simple examples, lots of comments, basic patterns
   - intermediate: Real-world patterns, some abstractions, tests
   - advanced: Production patterns, edge cases, performance considerations
8. Output ONLY the JSON object, no other text
"""


def generate_concept_repo_stream(concept: str, language: str, difficulty: str):
    """Stream-generate a teaching repo. Yields chunks of JSON text."""
    prompt = f"""Generate a teaching codebase for:

Concept: {concept}
Language: {language}
Difficulty: {difficulty}

Remember: output ONLY a valid JSON object with "name" and "files" array."""

    system = CONCEPT_LAB_SYSTEM
    full_text = ""
    for chunk in call_llm_stream(system, prompt):
        full_text += chunk
        yield chunk
    return full_text


def parse_generated_repo(text: str) -> Optional[dict]:
    """Parse the LLM output into a repo structure. Returns None on failure."""
    # Try to extract JSON from the text
    text = text.strip()
    # Remove markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
        text = text.strip()
    
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return None
        else:
            return None

    if not isinstance(data, dict) or "files" not in data:
        return None

    name = data.get("name", "concept-demo")
    files = []
    for f in data["files"]:
        if isinstance(f, dict) and "path" in f and "content" in f:
            files.append({
                "path": f["path"],
                "content": f["content"],
                "size": len(f["content"]),
                "is_priority": f["path"] in ("README.md", "main.py", "index.js", "main.go", "Main.java"),
            })

    if not files:
        return None

    # Build repo text (same format as ingested repos)
    import os
    language_stats = {}
    total_chars = 0
    for f in files:
        ext = os.path.splitext(f["path"])[1].lstrip(".")
        lang_map = {"py": "Python", "js": "JavaScript", "ts": "TypeScript", "go": "Go", "rs": "Rust", "java": "Java", "rb": "Ruby", "md": "Markdown"}
        lang = lang_map.get(ext, ext.upper() if ext else "Text")
        language_stats[lang] = language_stats.get(lang, 0) + 1
        total_chars += f["size"]

    sections = [f"# Project: {name}", f"Files: {len(files)}"]
    for f in files:
        ext = os.path.splitext(f["path"])[1].lstrip(".")
        sections.append(f'\n## {f["path"]}\n```{ext}\n{f["content"]}\n```')
    repo_text = "\n".join(sections)

    return {
        "name": name,
        "files": files,
        "text": repo_text,
        "data": {
            "name": name,
            "url": f"concept://{name}",
            "tree": "\n".join(f["path"] for f in files),
            "total_chars": total_chars,
            "file_count": len(files),
            "skipped": 0,
            "languages": language_stats,
        }
    }
