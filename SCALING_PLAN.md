# RepoLM Scaling Plan — Twitter Blowup Readiness

**Date:** 2026-02-28  
**Current state:** Single Railway container, 1 Gunicorn worker, SQLite, ~20 max concurrent SSE streams  
**Target:** 1,000+ concurrent users surviving a viral moment

---

## Executive Summary

RepoLM has several **well-designed** patterns (pure ASGI middleware, thread pool separation, circuit breaker, SSE semaphores, per-IP limits). But it has **fatal bottlenecks** that will crash or hang under 100+ concurrent users, let alone 1,000. The biggest issues are: SQLite write contention, single-process in-memory state, tiny thread pools, and unbounded memory from repo data stored in-process.

---

## 1. CRITICAL — Will Crash/Hang

### 1.1 SQLite Write Contention Under Load
**Files:** `src/db.py`, `src/state.py`  
**Problem:** Every request touches SQLite — rate limit checks, job status updates, token spending, anonymous usage tracking. SQLite allows only ONE writer at a time. With `busy_timeout=5000`, concurrent writes queue up for 5s then fail with "database locked". Under 100+ concurrent users doing ingests + generations + chats, the write queue backs up and requests start timing out or failing.  
**Evidence:** `db_retry()` only retries 3 times with 0.1-0.4s backoff. Rate limiting (`check_rate_limit_db`) does a read+write per request. `update_job()` writes on every progress callback during ingestion.

**Fix (TODAY — 2 hours):**
- Switch rate limiting to in-memory with `TTLDict` (already have the pattern). Only persist to DB on cleanup cycles.
- Batch job status updates: buffer in memory, flush every 2s instead of per-callback.
- Add connection pooling: replace per-call `sqlite3.connect()` with a module-level pool.

```python
# src/db.py — add connection pool
import queue as _queue

_pool = _queue.Queue(maxsize=10)

def get_db():
    try:
        conn = _pool.get_nowait()
        # Test connection is alive
        conn.execute("SELECT 1")
        return conn
    except (_queue.Empty, sqlite3.ProgrammingError):
        conn = sqlite3.connect(DB_PATH, timeout=30)  # increase from 10
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")  # increase from 5000
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn
```

**Fix (REFACTOR — 1 week):** Migrate to PostgreSQL on Railway ($7/mo addon). Eliminates write contention entirely. Every `sqlite3` call becomes an asyncpg call. This is the single highest-impact change.

### 1.2 Single Worker = Single Event Loop = Single Point of Failure
**File:** `Dockerfile` (`WORKERS=1`)  
**Problem:** One Gunicorn/Uvicorn worker means one Python process, one event loop. A single slow synchronous call or memory spike kills ALL connections. The thread pools (`generate_pool`, `ingest_pool`) share the GIL with the event loop.

**Fix (TODAY — 30 min):**
```dockerfile
# Dockerfile — change default
ENV WORKERS=4
```
**But this breaks everything** because `state.py` stores repos in-process `TTLDict`. Worker 1 ingests a repo, Worker 2 can't see it. The SQLite repo cache (`state.cache_repo_to_db`) partially handles this, but the in-memory `repos` dict is authoritative for active sessions.

**Fix (TODAY — 2 hours):** Keep WORKERS=1 but increase thread pools:
```bash
INGEST_WORKERS=4
GENERATE_WORKERS=8  
AUDIO_WORKERS=4
MAX_SSE_STREAMS=50
```

**Fix (REFACTOR — 3 days):** Move all shared state to Redis. Replace `TTLDict` stores with Redis hashes. Then scale to WORKERS=4+.

### 1.3 In-Memory Repo Storage = OOM Kill
**File:** `src/state.py` — `repos = TTLDict(max_size=20)`  
**Problem:** Each repo stores full file contents in memory (`files` list with `content` field). A medium repo is 5-20MB of text. 20 repos = 100-400MB. Under viral load, 20 concurrent users each ingesting a different repo fills this fast. Plus, `get_repo_with_fallback()` loads from SQLite back into memory, decompressing zlib blobs. Railway containers typically have 512MB-1GB RAM.

**Fix (TODAY — 1 hour):**
- Reduce `max_size` to 10
- Don't store file contents in the `repos` TTLDict — only store text summary and metadata. Load files on-demand from SQLite cache.
- Add memory monitoring to health endpoint

```python
# src/state.py — lazy file loading
repos = TTLDict(default_ttl=3600, name="repos", max_size=10)  # reduce TTL too

def get_repo_with_fallback(repo_id: str) -> Optional[dict]:
    repo = repos.get(repo_id)
    if repo:
        return repo
    db_repo = load_repo_from_db(repo_id)
    if db_repo and db_repo["status"] == "ready":
        # DON'T cache files in memory — only text and metadata
        lite = {k: v for k, v in db_repo.items() if k != "files"}
        lite["files"] = []  # files loaded on-demand
        repos.set(repo_id, lite)
        return db_repo  # return full for this request
    return None
```

### 1.4 `async_call_llm_stream` Busy-Polls the Event Loop  
**File:** `src/services/llm.py` — `async_call_llm_stream()`, `async_call_llm_stream_messages()`  
**Problem:** The `while True: q.get_nowait()` + `await asyncio.sleep(0.01)` pattern polls 100x/second per stream. With 20 concurrent SSE streams, that's 2,000 wakeups/second on the event loop doing nothing useful. This starves real I/O.

**Fix (TODAY — 1 hour):** Use `asyncio.Event` signaling instead of polling:

```python
async def async_call_llm_stream(prompt: str, content: str, model: str = DEFAULT_MODEL):
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    loop = asyncio.get_event_loop()

    def _producer():
        try:
            for chunk in call_llm_stream(prompt, content, model):
                loop.call_soon_threadsafe(q.put_nowait, chunk)
            loop.call_soon_threadsafe(q.put_nowait, _SENTINEL)
        except Exception as exc:
            loop.call_soon_threadsafe(q.put_nowait, exc)

    loop.run_in_executor(None, _producer)

    while True:
        item = await q.get()  # proper async wait, no polling
        if item is _SENTINEL:
            return
        if isinstance(item, Exception):
            raise item
        yield item
```

### 1.5 `_get_client()` Creates a New HTTP Client Per Call
**File:** `src/services/llm.py` — `_get_client()`  
**Problem:** Every LLM call creates a new `openai.OpenAI()` client, which creates a new `httpx` connection pool. Under load, this means hundreds of TCP connections being opened/closed, plus TLS handshakes. Will exhaust file descriptors.

**Fix (TODAY — 15 min):** Cache the client:
```python
_client = None
_client_lock = threading.Lock()

def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if os.environ.get("GEMINI_API_KEY"):
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
            _client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=REQUEST_TIMEOUT)
        else:
            _client = openai.OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT)
        return _client
```

---

## 2. HIGH — Will Degrade Badly

### 2.1 Git Clone Blocks Disk and CPU
**File:** `src/routes/repo.py` — `run_ingest()` calls `ingest_repo()` which does `git clone`  
**Problem:** Each repo clone hits disk I/O hard and can take 10-60s for large repos. With `INGEST_WORKERS=2`, only 2 repos clone at once. Queue depth of 50 means 50 users waiting, each holding an HTTP connection. Railway's ephemeral disk is slow.

**Fix (TODAY):** Already have queue with rejection. Increase `MAX_QUEUE_DEPTH` to 100, add estimated wait time to response.  
**Fix (REFACTOR):** Pre-clone popular repos. Use GitHub's tarball API instead of `git clone` (much faster, no .git history).

### 2.2 Rate Limiting via SQLite is Itself a Bottleneck
**File:** `src/db.py` — `check_rate_limit_db()`  
**Problem:** Every rate-limited request does: SELECT → parse JSON → filter timestamps → INSERT OR REPLACE. That's a full read-write transaction per request. With the JSON timestamps array pattern, this is also O(n) per window.

**Fix (TODAY — 1 hour):** Move to in-memory rate limiting:
```python
# src/routes/_helpers.py — use TTLDict or simple sliding window in memory
from collections import defaultdict
import time, threading

_rate_windows = defaultdict(list)
_rate_lock = threading.Lock()

def check_rate_limit_memory(key: str, max_requests: int, window: int) -> bool:
    now = time.time()
    with _rate_lock:
        _rate_windows[key] = [t for t in _rate_windows[key] if now - t < window]
        if len(_rate_windows[key]) >= max_requests:
            return True
        _rate_windows[key].append(now)
        return False
```

### 2.3 No CDN for Static Assets
**File:** `src/app.py` — `app.mount("/static", ...)`  
**Problem:** Every CSS, JS, image request hits the Python server. Under viral load, static assets compete with API requests for the single worker.

**Fix (TODAY — 30 min):** Add `Cache-Control` headers to static files. Better: serve through Railway's CDN or move static files to Cloudflare Pages/Vercel.

### 2.4 Template Reading from Disk on Every Request
**File:** `src/app.py` — `landing()`, `app_page()`, etc.  
**Problem:** `TEMPLATES_DIR.joinpath("landing.html").read_text()` does a disk read on every page load.

**Fix (TODAY — 30 min):** Cache templates at startup:
```python
_template_cache = {}
def get_template(name: str) -> str:
    if name not in _template_cache:
        _template_cache[name] = TEMPLATES_DIR.joinpath(name).read_text()
    return _template_cache[name]
```

### 2.5 SSE Semaphore is Global, Not Per-Worker
**File:** `src/concurrency.py` — `sse_semaphore = asyncio.Semaphore(20)`  
**Problem:** Only 20 simultaneous SSE streams across the entire server. Under viral load, this means only 20 users can stream content at once. Everyone else gets queued behind the semaphore (the `async with sse_semaphore` in generate.py).

**Fix (TODAY):** Increase to 100: `MAX_SSE_STREAMS=100`  
**Fix (REFACTOR):** Remove global semaphore, rely on per-IP limits + external load balancer limits.

---

## 3. MEDIUM — Will Limit Scale

### 3.1 TTLDict LRU Uses O(n) List Operations
**File:** `src/state.py` — `TTLDict._access_order.remove(key)`  
**Problem:** `list.remove()` is O(n). With `max_size=20` this is fine. If you increase `max_size` or use TTLDict for rate limiting, it becomes slow.

**Fix:** Use `OrderedDict` instead of list for LRU tracking (O(1) move-to-end).

### 3.2 No Request Queuing/Backpressure at Edge
**Problem:** Railway exposes the container directly. No request queuing at the load balancer level. If the server is overwhelmed, connections just fail.

**Fix:** Put Cloudflare in front (free tier). Enables: DDoS protection, static caching, request queuing, geographic distribution.

### 3.3 Repo Text Truncation is Wasteful
**File:** `src/routes/generate.py` — `text[:200_000]`  
**Problem:** Loading 200K chars into memory per request, then sending it all to the LLM. Under 50 concurrent generations, that's 10MB just in prompt strings.

**Fix:** Pre-truncate during ingestion. Store only the truncated version.

### 3.4 Chat History Unbounded in Prompt
**File:** `src/routes/generate.py` — `history[-12:]`  
**Problem:** 12 messages of chat history + full repo text = massive prompts. At $X/token with Gemini, this also burns money.

**Fix:** Summarize older history, limit repo context for chat.

### 3.5 No Graceful Degradation Strategy
**Problem:** When the server is overloaded, it just returns 503. No concept of "degrade to cached-only mode" or "disable non-essential features."

**Fix:** Add a load-shedding middleware that disables audio, slides, concept lab when pool utilization > 80%. Keep overview and chat working.

---

## Architecture Evaluation

### Can this scale on Railway alone?
**To ~200 concurrent users: YES**, with the TODAY fixes applied.  
**To 1,000: NO.** Railway's single-container model with SQLite fundamentally cannot handle it. You need:
1. PostgreSQL (Railway addon, $7/mo)
2. Redis (Railway addon, $5/mo)  
3. Multiple containers (Railway supports this but needs shared DB)

### Cheapest path to 1,000 concurrent users

| Step | Effort | Cost | Impact |
|------|--------|------|--------|
| 1. Cache LLM client | 15 min | $0 | Prevents FD exhaustion |
| 2. Fix async stream polling | 1 hour | $0 | 10x event loop efficiency |
| 3. Increase pool sizes + SSE limit | 15 min | $0 | 5x concurrent capacity |
| 4. In-memory rate limiting | 1 hour | $0 | Removes DB write pressure |
| 5. Template caching | 30 min | $0 | Faster page loads |
| 6. Cloudflare (free) in front | 30 min | $0 | DDoS, static CDN, edge caching |
| 7. Reduce in-memory repo footprint | 1 hour | $0 | Prevents OOM |
| 8. Railway PostgreSQL | 1 week | $7/mo | Eliminates SQLite bottleneck |
| 9. Railway Redis | 3 days | $5/mo | Multi-worker shared state |
| 10. Scale to 4 workers | 1 day | ~$20/mo | 4x throughput |

**Steps 1-7 can be done TODAY** and get you to ~200-300 concurrent users.  
**Steps 8-10 take 1-2 weeks** and get you to 1,000+.

### What breaks at each scale level

| Users | What breaks | Fix needed |
|-------|------------|------------|
| 50 | SSE semaphore (20 limit), LLM client FD exhaustion | Steps 1-3 |
| 100 | SQLite write contention, event loop starvation | Steps 2, 4 |
| 200 | Memory (repo data), slow rate limit checks | Steps 4, 7 |
| 500 | Single process ceiling, SQLite completely locked | Steps 8-9 |
| 1000 | Need horizontal scaling | Step 10 + possibly 2nd container |

---

## Recommended Action Plan

### Today (4-5 hours of work)
1. ✅ Cache the OpenAI client (15 min)
2. ✅ Fix `async_call_llm_stream` polling → proper async queue (1 hour)
3. ✅ Bump env vars: `GENERATE_WORKERS=8`, `INGEST_WORKERS=4`, `MAX_SSE_STREAMS=100` (15 min)
4. ✅ Move rate limiting to in-memory (1 hour)
5. ✅ Cache HTML templates at startup (30 min)
6. ✅ Reduce repo memory footprint (1 hour)
7. ✅ Put Cloudflare in front (30 min)

### This Week
8. Start PostgreSQL migration (biggest impact, most effort)
9. Add Redis for shared state

### Next Week
10. Multi-worker deployment
11. Load-shedding middleware
12. GitHub tarball API for faster ingestion

---

## Quick Wins Summary (Copy-Paste Ready)

### Environment Variables for Railway
```
WORKERS=1
INGEST_WORKERS=4
GENERATE_WORKERS=8
AUDIO_WORKERS=4
MAX_SSE_STREAMS=100
```

### Files to Change Today
1. `src/services/llm.py` — Cache client, fix async generators
2. `src/state.py` — Reduce `max_size`, lazy file loading
3. `src/routes/_helpers.py` — In-memory rate limiting
4. `src/app.py` — Template caching, static file cache headers
5. `src/concurrency.py` — (just env var changes)
