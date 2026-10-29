"""
Langfuse observability: wraps agent calls with tracing.
Every node execution is logged as a span in Langfuse.

Setup:
  1. `docker compose --profile observability up -d` (starts langfuse + langfuse-db)
  2. Get keys from http://localhost:3000 (Project Settings → API Keys)
  3. Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env
"""

import os
from contextlib import contextmanager

try:
    from langfuse import Langfuse
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False


def get_langfuse() -> "Langfuse | None":
    """Initialize Langfuse client if keys are set."""
    if not LANGFUSE_AVAILABLE:
        return None

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "http://langfuse:3000")

    if not public_key or not secret_key:
        return None

    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
    )


@contextmanager
def trace_agent_call(question: str, answer: str, route: str, latency_ms: float):
    """
    Context manager: traces a single agent call in Langfuse.
    Usage:
        with trace_agent_call(q, a, route, ms):
            pass  # (tracing happens on exit)
    """
    langfuse = get_langfuse()
    if langfuse is None:
        yield None
        return

    trace = langfuse.trace(
        name="agent_query",
        input={"question": question},
        output={"answer": answer},
        metadata={
            "route": route,
            "latency_ms": round(latency_ms, 1),
        },
        session_id="scs-dev",
        tags=["agent", "logistics"],
    )
    trace.update()
    yield trace


def flush_langfuse():
    """Flush pending Langfuse events (call at end of eval runs)."""
    langfuse = get_langfuse()
    if langfuse:
        langfuse.flush()
        print("✅ Langfuse events flushed")
   
