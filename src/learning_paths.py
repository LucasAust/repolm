"""
RepoLM — Curated Learning Paths
Collections of repos organized by concept for free learning content.
"""

LEARNING_PATHS = [
    {
        "id": "event-driven",
        "title": "Event-Driven Architecture",
        "description": "Learn how systems communicate through events — from simple pub/sub to full CQRS patterns.",
        "tags": ["architecture", "events", "messaging", "async"],
        "difficulty": "intermediate",
        "icon": "⚡",
        "repos": [
            {"url": "https://github.com/pallets/flask", "name": "Flask", "teaches": "Signal-based event system with blinker — see how Flask dispatches request lifecycle events."},
            {"url": "https://github.com/celery/celery", "name": "Celery", "teaches": "Distributed task queue built on message passing — the gold standard for async event processing in Python."},
            {"url": "https://github.com/apache/kafka", "name": "Apache Kafka", "teaches": "The definitive event streaming platform — topics, partitions, consumer groups, and exactly-once semantics."},
            {"url": "https://github.com/eventlet/eventlet", "name": "Eventlet", "teaches": "Concurrent networking via green threads and an event loop — lightweight event-driven I/O."},
        ]
    },
    {
        "id": "rest-api",
        "title": "REST API Design",
        "description": "Build clean, well-structured REST APIs. From routing basics to advanced patterns like HATEOAS.",
        "tags": ["api", "rest", "http", "web"],
        "difficulty": "beginner",
        "icon": "🌐",
        "repos": [
            {"url": "https://github.com/tiangolo/fastapi", "name": "FastAPI", "teaches": "Modern Python API framework with automatic OpenAPI docs, type validation, and async support."},
            {"url": "https://github.com/expressjs/express", "name": "Express.js", "teaches": "Minimalist Node.js framework — middleware chains, routing, and request/response lifecycle."},
            {"url": "https://github.com/encode/django-rest-framework", "name": "Django REST Framework", "teaches": "Full-featured REST toolkit — serializers, viewsets, permissions, pagination, and content negotiation."},
            {"url": "https://github.com/gin-gonic/gin", "name": "Gin", "teaches": "High-performance Go HTTP framework — shows how to build fast APIs with middleware and routing groups."},
        ]
    },
    {
        "id": "state-machines",
        "title": "State Machines",
        "description": "Understand finite state machines, statecharts, and state management patterns in real code.",
        "tags": ["state", "fsm", "patterns", "logic"],
        "difficulty": "intermediate",
        "icon": "🔄",
        "repos": [
            {"url": "https://github.com/statelyai/xstate", "name": "XState", "teaches": "JavaScript statecharts library — hierarchical states, guards, actions, and visualizable state machines."},
            {"url": "https://github.com/pytransitions/transitions", "name": "Transitions", "teaches": "Lightweight Python state machine — simple DSL for defining states, transitions, and callbacks."},
            {"url": "https://github.com/davidkpiano/flipping", "name": "Flipping", "teaches": "Animation state machine — shows how state machines drive UI transitions and FLIP animations."},
        ]
    },
    {
        "id": "auth-patterns",
        "title": "Authentication Patterns",
        "description": "OAuth, JWT, sessions, API keys — learn how real apps handle identity and access control.",
        "tags": ["security", "auth", "oauth", "jwt"],
        "difficulty": "intermediate",
        "icon": "🔐",
        "repos": [
            {"url": "https://github.com/nextauthjs/next-auth", "name": "NextAuth.js", "teaches": "Multi-provider auth for Next.js — OAuth flows, JWT sessions, database adapters, and CSRF protection."},
            {"url": "https://github.com/jpadilla/pyjwt", "name": "PyJWT", "teaches": "JWT encoding/decoding in Python — understand token structure, signing algorithms, and claims."},
            {"url": "https://github.com/ory/hydra", "name": "Ory Hydra", "teaches": "OpenID Connect and OAuth 2.0 server — consent flows, token introspection, and client credentials."},
            {"url": "https://github.com/passport/passport", "name": "Passport.js", "teaches": "Strategy-based auth middleware for Node.js — pluggable authentication with 500+ strategies."},
        ]
    },
    {
        "id": "database-orms",
        "title": "Database ORMs",
        "description": "Learn object-relational mapping — from query builders to full ORMs with migrations.",
        "tags": ["database", "orm", "sql", "models"],
        "difficulty": "beginner",
        "icon": "🗄️",
        "repos": [
            {"url": "https://github.com/sqlalchemy/sqlalchemy", "name": "SQLAlchemy", "teaches": "Python's most powerful ORM — Core expression language, ORM layer, session management, and migrations."},
            {"url": "https://github.com/prisma/prisma", "name": "Prisma", "teaches": "Next-gen Node.js ORM — schema-first design, type-safe queries, migrations, and introspection."},
            {"url": "https://github.com/sequelize/sequelize", "name": "Sequelize", "teaches": "Promise-based Node.js ORM — model definitions, associations, transactions, and eager loading."},
            {"url": "https://github.com/tortoise/tortoise-orm", "name": "Tortoise ORM", "teaches": "Async Python ORM inspired by Django — shows modern async database patterns."},
        ]
    },
    {
        "id": "cli-tools",
        "title": "CLI Tools",
        "description": "Build professional command-line interfaces — argument parsing, colors, progress bars, and interactive prompts.",
        "tags": ["cli", "terminal", "devtools"],
        "difficulty": "beginner",
        "icon": "⌨️",
        "repos": [
            {"url": "https://github.com/pallets/click", "name": "Click", "teaches": "Python CLI framework — decorators for commands, argument types, help generation, and plugin systems."},
            {"url": "https://github.com/tj/commander.js", "name": "Commander.js", "teaches": "Node.js CLI toolkit — subcommands, options parsing, auto-help, and custom argument processing."},
            {"url": "https://github.com/Textualize/rich", "name": "Rich", "teaches": "Beautiful terminal output in Python — tables, markdown, syntax highlighting, progress bars, and live displays."},
            {"url": "https://github.com/charmbracelet/bubbletea", "name": "Bubble Tea", "teaches": "Go TUI framework based on The Elm Architecture — shows how to build interactive terminal UIs."},
        ]
    },
    {
        "id": "web-scraping",
        "title": "Web Scraping",
        "description": "Extract data from websites — HTML parsing, headless browsers, anti-bot bypasses, and structured extraction.",
        "tags": ["scraping", "parsing", "automation", "data"],
        "difficulty": "beginner",
        "icon": "🕷️",
        "repos": [
            {"url": "https://github.com/scrapy/scrapy", "name": "Scrapy", "teaches": "Full scraping framework — spiders, pipelines, middleware, request scheduling, and distributed crawling."},
            {"url": "https://github.com/pydantic/pydantic", "name": "Pydantic", "teaches": "Data validation and parsing — essential for structuring scraped data into clean models."},
            {"url": "https://github.com/nicehash/Playwright", "name": "Playwright", "teaches": "Browser automation — headless Chrome/Firefox, network interception, and dynamic page scraping."},
            {"url": "https://github.com/psf/requests-html", "name": "Requests-HTML", "teaches": "Simple HTML parsing with CSS selectors and JavaScript rendering — quick scraping for small projects."},
        ]
    },
    {
        "id": "websockets",
        "title": "Real-Time WebSockets",
        "description": "Build real-time features — chat, live updates, multiplayer, and streaming with WebSockets.",
        "tags": ["realtime", "websocket", "streaming", "chat"],
        "difficulty": "intermediate",
        "icon": "🔌",
        "repos": [
            {"url": "https://github.com/socketio/socket.io", "name": "Socket.IO", "teaches": "Real-time engine with rooms, namespaces, auto-reconnect, and fallback transports."},
            {"url": "https://github.com/websockets/ws", "name": "ws", "teaches": "Minimal WebSocket implementation for Node.js — shows the raw protocol, framing, and connection lifecycle."},
            {"url": "https://github.com/centrifugal/centrifugo", "name": "Centrifugo", "teaches": "Real-time messaging server — scalable pub/sub with presence, history, and channel permissions."},
            {"url": "https://github.com/encode/starlette", "name": "Starlette", "teaches": "Python ASGI framework with native WebSocket support — see how async Python handles real-time."},
        ]
    },
    {
        "id": "testing-patterns",
        "title": "Testing Patterns",
        "description": "Write better tests — unit, integration, mocking, property-based testing, and test architecture.",
        "tags": ["testing", "tdd", "quality", "ci"],
        "difficulty": "intermediate",
        "icon": "🧪",
        "repos": [
            {"url": "https://github.com/pytest-dev/pytest", "name": "pytest", "teaches": "Python testing framework — fixtures, parametrize, plugins, and how to build a test runner from scratch."},
            {"url": "https://github.com/jestjs/jest", "name": "Jest", "teaches": "JavaScript testing with snapshots, mocking, code coverage, and parallel execution."},
            {"url": "https://github.com/HypothesisWorks/hypothesis", "name": "Hypothesis", "teaches": "Property-based testing — generate random test cases and shrink failures automatically."},
            {"url": "https://github.com/testcontainers/testcontainers-python", "name": "Testcontainers", "teaches": "Integration testing with Docker — spin up real databases and services for tests."},
        ]
    },
    {
        "id": "microservices",
        "title": "Microservices",
        "description": "Design, build, and deploy microservices — service discovery, API gateways, and distributed systems.",
        "tags": ["microservices", "distributed", "docker", "architecture"],
        "difficulty": "advanced",
        "icon": "🏗️",
        "repos": [
            {"url": "https://github.com/nameko/nameko", "name": "Nameko", "teaches": "Python microservices framework — RPC, events, HTTP, timers, and dependency injection."},
            {"url": "https://github.com/traefik/traefik", "name": "Traefik", "teaches": "Cloud-native API gateway — automatic service discovery, load balancing, and TLS."},
            {"url": "https://github.com/istio/istio", "name": "Istio", "teaches": "Service mesh — traffic management, security, and observability for microservices."},
            {"url": "https://github.com/go-kit/kit", "name": "Go Kit", "teaches": "Go microservices toolkit — endpoints, transports, middleware, and service discovery patterns."},
        ]
    },
]


def get_all_paths():
    """Return all learning paths (without full repo details for listing)."""
    return [{
        "id": p["id"],
        "title": p["title"],
        "description": p["description"],
        "tags": p["tags"],
        "difficulty": p["difficulty"],
        "icon": p["icon"],
        "repo_count": len(p["repos"]),
    } for p in LEARNING_PATHS]


def get_path_by_id(path_id: str):
    """Return a single learning path with full repo details."""
    for p in LEARNING_PATHS:
        if p["id"] == path_id:
            return p
    return None
