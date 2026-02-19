"""
RepoLM — Summarization Pipeline
Takes ingested repo text and produces structured summaries via LLM.
"""

import os
import json
import sys
from pathlib import Path

# We'll use OpenAI-compatible API (works with OpenAI, Anthropic via proxy, etc.)
# For MVP: use openai SDK pointed at whatever the user has
try:
    import openai
except ImportError:
    print("pip3 install openai")
    sys.exit(1)


OVERVIEW_PROMPT = """You are an expert software engineer and technical educator. 
Analyze this GitHub repository and produce a comprehensive, well-structured overview.

Your output should include:

## 1. What This Project Does
A clear, jargon-free explanation of the project's purpose. What problem does it solve? Who is it for?

## 2. Architecture Overview  
How is the codebase organized? What are the main components/modules and how do they interact?
Include a simple flow diagram if helpful (use ASCII or markdown).

## 3. Key Concepts
What programming concepts, patterns, or techniques does this project demonstrate?
(e.g., event-driven architecture, dependency injection, state machines, etc.)

## 4. How It Works (Step by Step)
Walk through the main execution flow. If someone runs this project, what happens from start to finish?

## 5. Notable Code Patterns
Highlight interesting or educational code patterns with brief explanations.
Reference specific files when relevant.

## 6. Tech Stack & Dependencies
What technologies, frameworks, and libraries does it use? Why these choices?

## 7. Learning Takeaways
What can someone learn from studying this codebase? What skills does it teach?

Write for a smart person who's learning. Be thorough but accessible.
Use concrete examples from the actual code — don't be generic.
"""

PODCAST_PROMPT = """You are a scriptwriter for a technical podcast called "RepoLM". 
Two hosts — Alex (enthusiastic, asks good questions) and Sam (deep technical knowledge, great at analogies) — are breaking down a GitHub repository.

Write a natural, engaging podcast script based on this repository overview.

Rules:
- Write it as a conversation, not a lecture
- Alex asks the questions a learner would ask
- Sam explains with analogies and real examples from the code
- Include moments of genuine enthusiasm when something is clever
- Keep it educational but entertaining — like friends geeking out
- 10-15 minutes of content (roughly 2000-3000 words)
- Start with a hook that makes people want to keep listening
- End with key takeaways and what listeners should explore first

Format each line as:
ALEX: [dialogue]
SAM: [dialogue]

Make it sound like real people talking, not robots reading docs.
"""

SLIDES_PROMPT = """You are creating a presentation deck about a GitHub repository.
Based on this overview, create a structured slide deck in markdown format.

Rules:
- 12-20 slides
- Each slide has a title and 3-5 bullet points max
- Include a "Key Takeaway" on most slides  
- Use code snippets where they help (keep them short — 3-5 lines max)
- Slide 1: Title + hook
- Slide 2: What & Why
- Middle slides: Architecture, concepts, code walkthrough
- Final slides: Learning takeaways, what to explore next

Format:
---
# Slide Title
- Bullet point
- Another point
```language
short code example
```
**Key Takeaway:** One sentence
---
"""


def _get_client():
    """Get OpenAI-compatible client configured for Gemini or OpenAI."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if os.environ.get("GEMINI_API_KEY"):
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        return openai.OpenAI(api_key=api_key, base_url=base_url)
    return openai.OpenAI(api_key=api_key)


def call_llm(prompt: str, content: str, model: str = "gemini-2.5-pro") -> str:
    """Call LLM via OpenAI-compatible API. Supports Gemini via Google's endpoint."""
    client = _get_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ],
        max_tokens=8192,
        temperature=0.7,
    )
    return response.choices[0].message.content


def call_llm_stream(prompt: str, content: str, model: str = "gemini-2.5-pro"):
    """Stream LLM response, yielding text chunks."""
    client = _get_client()
    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ],
        max_tokens=8192,
        temperature=0.7,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def call_llm_stream_messages(messages: list, model: str = "gemini-2.5-pro"):
    """Stream LLM response with a full messages array, yielding text chunks."""
    client = _get_client()
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=8192,
        temperature=0.7,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def generate_overview(repo_text: str, model: str = "gemini-2.5-pro") -> str:
    """Generate structured overview of the repo."""
    # If repo text is too long, truncate intelligently
    if len(repo_text) > 200_000:
        # Keep first 200K chars (priority files are first)
        repo_text = repo_text[:200_000] + "\n\n[... truncated for length ...]"

    print("Generating overview...")
    return call_llm(OVERVIEW_PROMPT, repo_text, model)


def generate_podcast(overview: str, model: str = "gemini-2.5-pro") -> str:
    """Generate podcast script from overview."""
    print("Generating podcast script...")
    return call_llm(PODCAST_PROMPT, overview, model)


def generate_slides(overview: str, model: str = "gemini-2.5-pro") -> str:
    """Generate slide deck from overview."""
    print("Generating slides...")
    return call_llm(SLIDES_PROMPT, overview, model)


def run_pipeline(repo_text_path: str, output_dir: str = "output", model: str = "gemini-2.5-pro",
                 formats: list = None):
    """Run the full summarization pipeline."""
    if formats is None:
        formats = ["overview", "podcast", "slides"]

    with open(repo_text_path, "r") as f:
        repo_text = f.read()

    repo_name = Path(repo_text_path).stem.replace("_raw", "")
    os.makedirs(output_dir, exist_ok=True)

    results = {}

    # Always generate overview first (others depend on it)
    overview = generate_overview(repo_text, model)
    overview_path = os.path.join(output_dir, f"{repo_name}_overview.md")
    with open(overview_path, "w") as f:
        f.write(overview)
    print(f"  Overview: {overview_path}")
    results["overview"] = overview_path

    if "podcast" in formats:
        script = generate_podcast(overview, model)
        script_path = os.path.join(output_dir, f"{repo_name}_podcast.md")
        with open(script_path, "w") as f:
            f.write(script)
        print(f"  Podcast: {script_path}")
        results["podcast"] = script_path

    if "slides" in formats:
        slides = generate_slides(overview, model)
        slides_path = os.path.join(output_dir, f"{repo_name}_slides.md")
        with open(slides_path, "w") as f:
            f.write(slides)
        print(f"  Slides: {slides_path}")
        results["slides"] = slides_path

    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python summarize.py <repo_raw.txt> [model]")
        sys.exit(1)

    repo_text_path = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "gemini-2.5-pro"
    run_pipeline(repo_text_path, model=model)
