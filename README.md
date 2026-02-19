# RepoLM

**Learn any codebase in minutes.** Paste a GitHub repo URL — get overviews, podcast-style explanations, slide decks, and interactive learning tools, all powered by AI.

![RepoLM Screenshot](docs/screenshot.png)
<!-- TODO: Add a screenshot of the app page showing an overview and podcast for a popular repo -->

## Features

- 📖 **Overview & Slides** — Architecture breakdowns and presentation-ready slide decks generated from actual code
- 🎙️ **Podcast Mode** — Two AI hosts break down the codebase in a conversational, engaging format with TTS audio
- 🔍 **Immersive Mode** — Highlight code and ask questions, like pair programming with an expert
- 🧪 **Concept Lab** — Interactive experiments and exercises to deepen understanding
- 📚 **Learning Paths** — Curated topic-based journeys across multiple repos
- 🎚️ **Adaptive Depth** — Beginner, amateur, or expert level — content adapts to you
- 🔗 **Shareable** — Public share links for any generated content
- 🧑‍💻 **Developer API** — REST API with API key authentication
- 💳 **Pro Tier** — Unlimited tokens via Stripe-powered subscriptions

## Quick Start

### Prerequisites

- Python 3.9+
- Git
- A [Google Gemini API key](https://ai.google.dev/)

### Local Development

```bash
git clone https://github.com/yourusername/repolm.git
cd repolm

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your API keys

# Run the app
cd src
uvicorn app:app --reload --port 8000
```

Visit [http://localhost:8000](http://localhost:8000)

### Docker

```bash
docker compose up
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `JWT_SECRET` | Yes | Secret for JWT token signing |
| `STRIPE_SECRET_KEY` | No | Stripe secret key (for payments) |
| `STRIPE_WEBHOOK_SECRET` | No | Stripe webhook signing secret |
| `STRIPE_PRICE_ID` | No | Stripe price ID for Pro plan |
| `GITHUB_TOKEN` | No | GitHub personal access token (higher rate limits) |
| `ELEVENLABS_API_KEY` | No | ElevenLabs API key (for TTS audio) |
| `DATA_DIR` | No | Directory for SQLite DB and data (default: `./data`) |
| `CARBON_SERVE` | No | Carbon Ads serve ID |
| `CARBON_PLACEMENT` | No | Carbon Ads placement ID |

## Deployment (Railway)

RepoLM is configured for one-click Railway deployment:

```bash
# railway.json is already configured
railway up
```

Set environment variables in the Railway dashboard. The app uses SQLite with a persistent volume at `/app/data`.

## Tech Stack

- **Backend**: FastAPI + Uvicorn (Python 3.9)
- **AI**: Google Gemini (code analysis, content generation)
- **TTS**: ElevenLabs (podcast audio)
- **Frontend**: Alpine.js + Tailwind CSS (CDN, no build step)
- **Database**: SQLite (WAL mode)
- **Payments**: Stripe
- **Deployment**: Docker + Railway

## Project Structure

```
src/
├── app.py              # FastAPI app, middleware, routes
├── routes/             # Route modules (generate, audio, slides, etc.)
├── services/           # LLM, audio generation, ingestion
├── templates/          # HTML templates (Alpine.js + Tailwind)
├── static/             # Favicon, static assets
├── output/examples/    # Pre-generated example content
├── db.py               # SQLite database
├── auth.py             # Authentication (JWT, OAuth)
├── payments.py         # Stripe integration
├── config.py           # Configuration
└── concurrency.py      # Thread pool management
```

## License

MIT
