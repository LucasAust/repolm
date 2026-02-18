# 🦞 RepoLM

Turn any GitHub repo into learning content — overviews, podcasts, slides.

Like NotebookLM, but for codebases.

## Quick Start

```bash
pip3 install openai edge-tts
cd src
python cli.py https://github.com/expressjs/express
python cli.py https://github.com/redis/redis --audio
```

## Output Formats

- **Overview** — Structured markdown breakdown of architecture, concepts, and code patterns
- **Podcast** — Two-host conversational script (+ audio generation via TTS)
- **Slides** — Presentation-ready markdown slide deck

## How It Works

1. **Ingest** — Clones repo, smart-filters files, builds LLM-friendly text representation
2. **Summarize** — LLM generates structured overview, podcast script, and/or slides
3. **Audio** — TTS converts podcast script to MP3 (edge-tts free, ElevenLabs premium)

## Roadmap

- [ ] Web UI (paste URL, get content)
- [ ] Pre-built course packs for popular repos
- [ ] Interactive Q&A ("ask questions about this repo")
- [ ] Video generation from slides
- [ ] Quizzes and exercises
