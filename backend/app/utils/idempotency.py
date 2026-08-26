"""
Idempotency for critical serverless operations (spec 79).

A client may retry a score submission or an import after a timeout without
knowing whether the first attempt landed. When the request carries an
`Idempotency-Key` header, the first response is stored and replayed for any
repeat of the same key, so retries cannot create duplicate results, matches,
standings or notifications.

Reusing a key with a *different* payload is rejected rather than silently
returning the old answer.
"""
from typing import Any, Dict, Optional
from fastapi import Header, HTTPException, Request
import hashlib
import json
import logging

logger = logging.getLogger("uvicorn.error")

# Set the first time the key store is touched, so /api/health can report
# whether retry protection is actually active.
_store_available: Optional[bool] = None


def idempotency_store_available() -> Optional[bool]:
    """True/False once probed, None before the first guarded request."""
    return _store_available


def _mark_store(available: bool) -> None:
    global _store_available
    if _store_available != available and not available:
        logger.warning(
            "idempotency_keys table unavailable — retries are NOT deduplicated. "
            "Apply backend/db/migrations/002_serverless_architecture.sql."
        )
    _store_available = available


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


async def get_idempotency_key(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Optional[str]:
    return idempotency_key


class IdempotencyGuard:
    """
    Usage:
        guard = IdempotencyGuard(admin_db, key, endpoint, payload)
        cached = guard.replay()
        if cached is not None:
            return cached
        ... do the work ...
        guard.store(result)
        return result

    With no key supplied the guard is inert, so callers need no branching.
    """

    def __init__(self, admin_db, key: Optional[str], endpoint: str, payload: Any):
        self.admin_db = admin_db
        self.key = key
        self.endpoint = endpoint
        self.request_hash = _hash_payload(payload) if key else ""
        self.enabled = bool(key)

    def replay(self) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        try:
            existing = self.admin_db.table("idempotency_keys").select("*").eq(
                "key", self.key
            ).execute().data
        except Exception as e:
            # A missing table (migration not yet applied) must not take the
            # endpoint down; it only means retries are unprotected.
            logger.warning(f"Idempotency lookup failed for {self.endpoint}: {str(e)}")
            _mark_store(False)
            self.enabled = False
            return None

        _mark_store(True)

        if not existing:
            return None

        record = existing[0]
        if record["request_hash"] != self.request_hash:
            raise HTTPException(
                status_code=409,
                detail="This Idempotency-Key was already used with a different request body.",
            )
        return record.get("response")

    def store(self, response: Any, status_code: int = 200) -> None:
        if not self.enabled:
            return
        try:
            self.admin_db.table("idempotency_keys").upsert({
                "key": self.key,
                "endpoint": self.endpoint,
                "request_hash": self.request_hash,
                "status_code": status_code,
                "response": response,
            }).execute()
        except Exception as e:
            logger.warning(f"Idempotency store failed for {self.endpoint}: {str(e)}")
