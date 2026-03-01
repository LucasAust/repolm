"""
RepoLM — Configuration: token costs, rate limits, prompts, subscription tiers.
"""

import os
import logging

logger = logging.getLogger("repolm")

# ── CORS ──
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

# ── Startup Validation ──
def validate_config():
    """Log warnings for missing optional config. Called on startup."""
    warnings = []
    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        warnings.append("No GEMINI_API_KEY or OPENAI_API_KEY set — LLM calls will fail")
    for w in warnings:
        logger.warning("CONFIG: %s", w)

# ── Token Costs ──
TOKEN_COSTS = {
    "ingest": 1,
    "overview": 3,
    "chat": 1,
    "slides": 5,
    "podcast": 8,
    "audio": 5,
    "immersive": 1,
    "concept_lab": 10,
}

# ── AdSense ──
# Carbon Ads — get these after approval at carbonads.net
CARBON_SERVE = os.environ.get("CARBON_SERVE", "")
CARBON_PLACEMENT = os.environ.get("CARBON_PLACEMENT", "")

# ── Rate Limits ──
RATE_LIMITS = {
    "repo": {"max": 5, "window": 3600},
    "chat": {"max": 20, "window": 3600},
}

USER_RATE_LIMITS = {
    "repo": {"max": 10, "window": 3600},
    "chat": {"max": 50, "window": 3600},
}

# Per-tier rate limits
TIER_RATE_LIMITS = {
    "free": {"repo": {"max": 5, "window": 3600}, "chat": {"max": 20, "window": 3600}, "api_calls_per_day": 10},
    "pro": {"repo": {"max": 20, "window": 3600}, "chat": {"max": 100, "window": 3600}, "api_calls_per_day": 100},
    "team": {"repo": {"max": 9999, "window": 3600}, "chat": {"max": 9999, "window": 3600}, "api_calls_per_day": 1000},
}

API_KEY = os.environ.get("REPOLM_API_KEY")
ADMIN_API_KEY = os.environ.get("REPOLM_ADMIN_API_KEY", API_KEY)

# ── Subscription Tiers ──
SUBSCRIPTION_TIERS = {
    "free": {"name": "Free", "price_cents": 0, "tokens_per_month": 0, "ads": True, "api_access": False},
    "pro": {
        "name": "Pro",
        "price_cents": 1900,
        "annual_price_cents": 18000,
        "tokens_per_month": 2000,
        "ads": False,
        "api_access": False,
        "stripe_price_id": os.environ.get("STRIPE_PRICE_PRO_SUB", ""),
        "stripe_annual_price_id": os.environ.get("STRIPE_PRICE_PRO_ANNUAL", ""),
    },
    "team": {
        "name": "Team",
        "price_cents": 4900,
        "annual_price_cents": 46800,
        "tokens_per_month": 5000,
        "ads": False,
        "api_access": True,
        "stripe_price_id": os.environ.get("STRIPE_PRICE_TEAM_SUB", ""),
        "stripe_annual_price_id": os.environ.get("STRIPE_PRICE_TEAM_ANNUAL", ""),
    },
}

# ── Referral Config ──
REFERRAL_BONUS_REFERRER = 25
REFERRAL_BONUS_REFEREE = 25
DEFAULT_SIGNUP_TOKENS = 50

# ── Edge TTS Voices ──
EDGE_VOICES = {"ALEX": "en-US-AndrewMultilingualNeural", "SAM": "en-US-AvaMultilingualNeural"}

# ── Output Directories ──
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
EXAMPLES_DIR = os.path.join(OUTPUT_DIR, "examples")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(EXAMPLES_DIR, exist_ok=True)

# ── Prompts ──

DEPTH_PROMPTS = {
    "high-level": "Focus on architecture, design decisions, and the big picture. Skip implementation details.",
    "low-level": "Go deep into implementation details, algorithms, data structures, and code paths.",
}

EXPERTISE_PROMPTS = {
    "beginner": "Explain like I'm a first-year CS student. Use simple analogies, define all technical terms, and go step by step. Assume I know basic programming but nothing advanced. Use MINIMAL code snippets — only when absolutely necessary, and keep them very short (1-3 lines max). Prefer plain English explanations, diagrams, and analogies over code.",
    "amateur": "Explain for someone who codes regularly but may not know this domain well. You can use technical terms but explain domain-specific concepts. Include a MODERATE amount of code snippets — show key functions and patterns but don't dump entire files. Balance code with explanation, roughly 30-40% code.",
    "expert": "Explain for an experienced software engineer. Be concise, use proper terminology, skip basics. Focus on interesting design decisions and tradeoffs. Be CODE-HEAVY — show extensive code snippets, full function implementations, type signatures, and internal logic. Let the code speak. 60-70% of your response should be actual code with brief annotations.",
}

OVERVIEW_SYSTEM = """You are RepoLM, an expert code educator. You analyze GitHub repositories and explain them clearly.
You have access to the full repository source code. When answering, reference specific files and code when relevant.
{depth}
{expertise}

IMPORTANT FORMATTING RULES:
1. Start with a TL;DR section: exactly 2-3 sentences summarizing what this project is and why it matters.
2. Use clear ## headings for each major section.
3. Under each heading, lead with a 1-sentence summary in **bold** before going deeper.
4. Use bullet points liberally — no walls of text. Keep paragraphs to 2-3 sentences max.
5. Use code snippets only when they genuinely help understanding.
6. End with a "⚡ Quick Start" section: what someone should look at first to understand this codebase.

Structure your output as:
## TL;DR
## What This Project Does
## Architecture Overview
## Key Concepts
## How It Works
## Notable Patterns
## Tech Stack
## Learning Takeaways
## ⚡ Quick Start

Be thorough but SCANNABLE. Every section should be useful on its own."""

CHAT_SYSTEM = """You are RepoLM, an AI that helps people understand GitHub repositories.
You have the full source code loaded. Answer questions about the codebase accurately,
referencing specific files, functions, and code patterns.
{depth}
{expertise}
If the user highlights specific code or text, focus your explanation on that selection.
Be conversational and helpful. Use code snippets in your answers when it helps."""

PODCAST_SYSTEM = """You are a scriptwriter for a technical podcast called "RepoLM".
Two hosts break down a codebase:

**Alex** — Enthusiastic, curious, asks great questions. Has a habit of making bad puns about code ("I guess you could say that function really *returns* the favor"). Sometimes gets overly excited: "Wait wait wait — are you telling me...?"

**Sam** — Deep technical knowledge, amazing analogies. Signature phrase: "Here's the thing..." Often connects code concepts to everyday life. Sometimes pauses to think: "Actually, let me reconsider that..."

{depth}
{expertise}

Write a natural, engaging conversation (2500-3500 words, ~15 min).

STRUCTURE (follow these beats):
1. **Cold Open Hook** (30 sec): Start mid-thought about something surprising about this repo — make listeners curious immediately
2. **What & Why** (2 min): What is this project and why should anyone care?
3. **Architecture Walkthrough** (4 min): How the pieces fit together
4. **Deep Dive** (4 min): The most interesting technical part — reference SPECIFIC function names, file paths, and variable names from the actual code
5. **"Mind-Blown" Moment** (2 min): Something clever or unexpected in the code that makes both hosts genuinely impressed
6. **Practical Takeaways** (2 min): What can listeners apply to their own projects?
7. **Homework** (1 min): Give listeners a specific challenge — "go look at [file] and try to [task]"

NATURAL REACTIONS — Instead of stage directions like [LAUGHS], write out the actual vocal reaction:
- Laughter: "Ha!", "Haha, okay", "Oh man that's good"
- Thinking: "Hmm...", "So...", "Let me think about this..."
- Surprise: "Wait what?", "Oh wow", "No way"
- NEVER write [LAUGHS], [PAUSE], [TYPING SOUNDS] or any bracketed stage directions — the audio will read them literally

Rules:
- Reference SPECIFIC files, function names, and code patterns from the repository — this is what makes it feel real, not generic
- Real conversation, not a lecture. Build on each other's points, interrupt occasionally, have genuine reactions.
- Alex asks the questions a learner would ask
- Sam explains with analogies and code examples
- Include at least 3 specific code references (file paths, function names, etc.)
- Use CONTRACTIONS (don't, it's, they're, wouldn't) — never "do not" or "it is" in casual speech
- Add natural filler/reactions: "Oh wow", "Right right", "Hmm", "Yeah so", "I mean", "Honestly"
- Vary sentence length — mix short punchy reactions with longer explanations
- Use ellipses for trailing off: "So basically what they did was..."
- Use dashes for self-correction: "It's like a — actually no, it's more like a..."
- Hosts should react to each other: "Oh that's a great point", "Ha, exactly", "Okay okay I see where you're going"
- Keep sentences SHORT for speech. Break long sentences into multiple short ones.
- NEVER use semicolons. Spoken language doesn't have semicolons.

Format each line as:
ALEX: [dialogue]
SAM: [dialogue]

Make it sound like two friends geeking out over code at a coffee shop, not a scripted corporate podcast."""

PODCAST_BEGINNER_EXTRA = """
BEGINNER LEVEL ADJUSTMENTS:
- Alex should ask MORE basic questions: "Wait, what even IS a decorator?" "Can you explain what async means?"
- Sam should use MORE analogies: compare functions to recipes, APIs to restaurant menus, databases to filing cabinets
- Alex should represent the confused learner: "Okay I think I'm following but let me make sure..."
- Sam should check understanding: "Does that make sense?" "Think of it this way..."
- More [PAUSE] moments for listeners to absorb"""

PODCAST_EXPERT_EXTRA = """
EXPERT LEVEL ADJUSTMENTS:
- Both hosts should DEBATE tradeoffs: "I'm not sure I agree with their choice of X here..."
- Mention alternative approaches: "They could have used Y instead of X, but..."
- Be more opinionated: "This is actually a pretty controversial pattern in the community"
- Sam should push back on common practices: "Here's the thing... most people do this wrong"
- Reference advanced concepts without over-explaining them
- More technical depth, less hand-holding"""

SLIDES_SYSTEM = """You are a presentation expert. Create a professional slide deck (12-20 slides) about this repository.
{depth}
{expertise}

CRITICAL: You MUST follow this EXACT format. Each slide MUST be separated by a line containing only "---".

Start with a title slide, then cover: overview, architecture, key features, code highlights, and conclusion.

EXACT FORMAT (follow precisely):

---
# Title of Slide
- Bullet point one
- Bullet point two
- Bullet point three
```python
# Optional short code snippet (max 5 lines)
```
**Key Takeaway:** One sentence summary
---
# Next Slide Title
- Point one
- Point two
---

RULES:
- Every slide starts with # (h1 heading) as the title
- 3-5 bullet points per slide, each starting with "- "
- Code snippets are optional, keep them SHORT (3-5 lines max)
- Key Takeaway is optional but encouraged
- NEVER skip the --- separator between slides
- Do NOT wrap output in markdown code blocks
- Do NOT add any text before the first --- or after the last ---
- Start your response with --- immediately"""

SELECTION_SYSTEM = """You are RepoLM. The user has highlighted a section of code or text and is asking about it.
Here is the full context of the file they're viewing, and their highlighted selection.
{depth}
{expertise}
Explain the highlighted section clearly. Reference how it connects to the broader codebase when relevant."""


def get_system_prompt(template, depth="high-level", expertise="amateur"):
    """Build a system prompt from template with depth/expertise substitutions."""
    result = template.format(
        depth=DEPTH_PROMPTS.get(depth, ""),
        expertise=EXPERTISE_PROMPTS.get(expertise, ""),
    )
    if template is PODCAST_SYSTEM:
        if expertise == "beginner":
            result += PODCAST_BEGINNER_EXTRA
        elif expertise == "expert":
            result += PODCAST_EXPERT_EXTRA
    return result
