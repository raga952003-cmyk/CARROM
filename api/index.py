"""
Vercel Python serverless entrypoint (spec 56, 59 Option B, 87).

Vercel discovers `app` in this module and serves the FastAPI ASGI application as
a serverless function. Nothing here holds per-request state, so concurrent cold
and warm invocations are safe; the Supabase clients are module-level and are
reused across warm invocations rather than rebuilt per request.

Locally the same app is served by uvicorn (see .claude/launch.json), so there is
one codebase and one behaviour in both places.
"""
import os
import sys
from pathlib import Path

# The FastAPI application lives in backend/app; make it importable regardless of
# the working directory the platform chooses.
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402

# Vercel looks for a module-level ASGI callable named `app` or `handler`.
handler = app

__all__ = ["app", "handler"]
