"""
RepoLM — LLM service: call_llm, call_llm_stream, call_llm_stream_messages.
Wraps OpenAI-compatible API (Gemini via Google endpoint or OpenAI direct).
Includes retry logic, circuit breaker, and timeouts.
"""

import os
import sys
import time
import logging
import threading

logger = logging.getLogger("repolm")

try:
    import openai
except ImportError:
    print("pip3 install openai")
    sys.exit(1)


# ── Circuit Breaker ──
class CircuitBreaker:
    """Simple circuit breaker: opens after N consecutive failures, resets after cooldown."""

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 60.0):
        self._lock = threading.Lock()
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._open_until = 0.0  # timestamp when circuit closes again
        self._total_calls = 0
        self._total_errors = 0
        self._latencies = []  # last 100

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._open_until > time.time():
                return True
            return False

    def record_success(self, latency: float):
        with self._lock:
            self._failure_count = 0
            self._total_calls += 1
            self._latencies.append(latency)
            if len(self._latencies) > 100:
                self._latencies = self._latencies[-100:]

    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            self._total_errors += 1
            self._total_calls += 1
            if self._failure_count >= self._failure_threshold:
                self._open_until = time.time() + self._cooldown
                logger.warning("Circuit breaker OPEN — %d consecutive failures, cooling down %.0fs",
                               self._failure_count, self._cooldown)

    def stats(self) -> dict:
        with self._lock:
            avg_latency = sum(self._latencies) / len(self._latencies) if self._latencies else 0
            return {
                "total_calls": self._total_calls,
                "total_errors": self._total_errors,
                "error_rate": round(self._total_errors / max(self._total_calls, 1), 3),
                "avg_latency_ms": round(avg_latency * 1000, 1),
                "circuit_open": self.is_open,
                "consecutive_failures": self._failure_count,
            }


_circuit = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)

# ── Retry Config ──
RETRY_STATUS_CODES = {429, 500, 502, 503}
RETRY_DELAYS = [2, 4, 8]  # exponential backoff
REQUEST_TIMEOUT = 120  # seconds


def get_circuit_stats() -> dict:
    return _circuit.stats()


def _get_client():
    """Get OpenAI-compatible client configured for Gemini or OpenAI."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if os.environ.get("GEMINI_API_KEY"):
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        return openai.OpenAI(api_key=api_key, base_url=base_url, timeout=REQUEST_TIMEOUT)
    return openai.OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT)


def _is_retryable(exc) -> bool:
    """Check if an exception is retryable."""
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in RETRY_STATUS_CODES
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return True
    return False


def call_llm(prompt: str, content: str, model: str = "gemini-2.5-pro") -> str:
    """Call LLM with system prompt and user content. Returns full response. Retries on transient errors."""
    if _circuit.is_open:
        raise RuntimeError("Service temporarily unavailable (circuit breaker open)")

    client = _get_client()
    last_exc = None

    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            start = time.time()
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": content},
                ],
                max_tokens=8192,
                temperature=0.7,
            )
            latency = time.time() - start
            _circuit.record_success(latency)
            return response.choices[0].message.content
        except Exception as e:
            last_exc = e
            if _is_retryable(e) and attempt < len(RETRY_DELAYS):
                delay = RETRY_DELAYS[attempt]
                logger.warning("LLM call failed (attempt %d/%d), retrying in %ds: %s",
                               attempt + 1, len(RETRY_DELAYS) + 1, delay, e)
                time.sleep(delay)
                continue
            _circuit.record_failure()
            raise

    _circuit.record_failure()
    raise last_exc


def call_llm_stream(prompt: str, content: str, model: str = "gemini-2.5-pro"):
    """Stream LLM response, yielding text chunks. Retries on transient errors."""
    if _circuit.is_open:
        raise RuntimeError("Service temporarily unavailable (circuit breaker open)")

    client = _get_client()
    last_exc = None

    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            start = time.time()
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
            chunk_count = 0
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    chunk_count += 1
                    yield chunk.choices[0].delta.content
            latency = time.time() - start
            _circuit.record_success(latency)
            return  # success
        except Exception as e:
            last_exc = e
            if _is_retryable(e) and attempt < len(RETRY_DELAYS):
                delay = RETRY_DELAYS[attempt]
                logger.warning("LLM stream failed (attempt %d/%d), retrying in %ds: %s",
                               attempt + 1, len(RETRY_DELAYS) + 1, delay, e)
                time.sleep(delay)
                continue
            _circuit.record_failure()
            raise

    _circuit.record_failure()
    raise last_exc


def call_llm_stream_messages(messages: list, model: str = "gemini-2.5-pro"):
    """Stream LLM response with a full messages array, yielding text chunks."""
    if _circuit.is_open:
        raise RuntimeError("Service temporarily unavailable (circuit breaker open)")

    client = _get_client()
    last_exc = None

    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            start = time.time()
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
            latency = time.time() - start
            _circuit.record_success(latency)
            return
        except Exception as e:
            last_exc = e
            if _is_retryable(e) and attempt < len(RETRY_DELAYS):
                delay = RETRY_DELAYS[attempt]
                logger.warning("LLM stream_messages failed (attempt %d/%d), retrying in %ds: %s",
                               attempt + 1, len(RETRY_DELAYS) + 1, delay, e)
                time.sleep(delay)
                continue
            _circuit.record_failure()
            raise

    _circuit.record_failure()
    raise last_exc
