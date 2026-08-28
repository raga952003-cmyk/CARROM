"""
Transactional write paths (spec 71, 77).

The deterministic maths stays in the Python engines; these helpers exist so the
resulting *writes* commit atomically. Each wraps a Postgres function from
`db/migrations/002_serverless_architecture.sql`.

If that migration has not been applied yet the helpers fall back to sequential
writes and log a loud warning — the app keeps working, but without atomicity.
`GET /api/health` reports which mode is active.
"""
import time
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger("uvicorn.error")

# Postgrest reports an unknown function as PGRST202.
_MISSING_FUNCTION_MARKERS = ("PGRST202", "could not find the function", "does not exist")

_rpc_available: Optional[bool] = None


def _looks_like_missing_function(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker.lower() in message for marker in _MISSING_FUNCTION_MARKERS)


def transactional_rpc_available() -> Optional[bool]:
    """True/False once probed, None before the first attempt."""
    return _rpc_available


def _mark(available: bool) -> None:
    global _rpc_available
    if _rpc_available != available:
        if not available:
            logger.warning(
                "Transactional RPCs are unavailable — falling back to sequential writes. "
                "Apply backend/db/migrations/002_serverless_architecture.sql to restore atomicity."
            )
        else:
            logger.info("Transactional RPCs available; score writes are atomic.")
    _rpc_available = available



# Supabase occasionally drops a pooled connection mid-request, surfacing as
# "Server disconnected" or a read timeout. Losing a board score to that is the
# worst outcome in the app — the game was played and the record is not there —
# and the write is a single atomic RPC, so retrying it once is safe.
_TRANSIENT = (
    "server disconnected",
    "connection reset",
    "connection aborted",
    "remote end closed",
    "read timeout",
    "timed out",
    "connection refused",
    "temporarily unavailable",
)


def _looks_transient(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT)


def _with_retry(call, attempts: int = 3, delay: float = 0.4):
    """Run `call`, retrying only failures that look like a dropped connection."""
    last = None
    for attempt in range(attempts):
        try:
            return call()
        except Exception as e:
            last = e
            if not _looks_transient(e) or attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))
    raise last


def apply_board_result(
    admin_db,
    *,
    match_id: str,
    board_number: int,
    board_patch: Dict[str, Any],
    match_patch: Dict[str, Any],
    audit: Dict[str, Any],
    next_board_number: Optional[int] = None,
    set_number: Optional[int] = None,
) -> Dict[str, Any]:
    """Board row + audit row + match aggregates + next board, in one transaction."""
    try:
        result = _with_retry(lambda: admin_db.rpc("apply_board_result", {
            "p_match_id": match_id,
            "p_board_number": board_number,
            "p_board_patch": board_patch,
            "p_match_patch": match_patch,
            "p_audit": audit,
            "p_next_board_number": next_board_number,
            # Board numbers restart each set, so the set is part of the board's
            # identity; without it the lock matches every set at once.
            "p_set_number": set_number or 1,
        }).execute())
        _mark(True)
        return result.data or {}
    except Exception as e:
        if not _looks_like_missing_function(e):
            raise
        _mark(False)
        return _apply_board_result_fallback(
            admin_db, match_id, board_number, board_patch, match_patch, audit,
            next_board_number, set_number,
        )


def _apply_board_result_fallback(
    admin_db, match_id, board_number, board_patch, match_patch, audit, next_board_number,
    set_number=None,
) -> Dict[str, Any]:
    def board_query(q):
        q = q.eq("match_id", match_id).eq("board_number", board_number)
        # Only narrow by set when the match is played in sets; the column does
        # not exist on databases before migration 006.
        return q.eq("set_number", set_number) if set_number else q

    previous = board_query(admin_db.table("boards").select("*")).execute().data
    prev = previous[0] if previous else {}

    updated = board_query(admin_db.table("boards").update(board_patch)).execute()

    admin_db.table("score_audit_logs").insert({
        "match_id": match_id,
        "admin_id": audit.get("admin_id"),
        "admin_name": audit.get("admin_name", "System"),
        "board_number": board_number,
        "previous_score": {
            "player1": prev.get("player1_score", 0),
            "player2": prev.get("player2_score", 0),
        },
        "new_score": audit.get("new_score", {}),
        "reason": audit.get("reason", "Score update"),
    }).execute()

    admin_db.table("matches").update(match_patch).eq("id", match_id).execute()

    if next_board_number is not None and match_patch.get("status") != "completed":
        q = admin_db.table("boards").update({"status": "in_progress"}).eq(
            "match_id", match_id
        ).eq("board_number", next_board_number).eq("status", "pending")
        if set_number:
            q = q.eq("set_number", set_number)
        q.execute()

    return updated.data[0] if updated.data else {}


def confirm_match_result(
    admin_db,
    *,
    match_id: str,
    actor_id: Optional[str],
    actor_name: str,
    notifications: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Confirm + advance winner + notify + audit, atomically.

    Idempotent: confirming an already-confirmed match returns
    `already_confirmed: true` without re-advancing or re-notifying.
    """
    try:
        result = admin_db.rpc("confirm_match_result", {
            "p_match_id": match_id,
            "p_actor_id": actor_id,
            "p_actor_name": actor_name,
            "p_notifications": notifications,
        }).execute()
        _mark(True)
        return result.data or {}
    except Exception as e:
        if not _looks_like_missing_function(e):
            raise
        _mark(False)
        return _confirm_match_result_fallback(
            admin_db, match_id, actor_id, actor_name, notifications
        )


def _confirm_match_result_fallback(
    admin_db, match_id, actor_id, actor_name, notifications
) -> Dict[str, Any]:
    from app.services.audit_service import record_audit

    rows = admin_db.table("matches").select("*").eq("id", match_id).execute().data
    if not rows:
        raise ValueError(f"match_not_found: {match_id}")
    match = rows[0]

    if match.get("result_confirmed"):
        return {"confirmed": True, "advanced": False, "already_confirmed": True,
                "match_id": match_id}

    from datetime import datetime
    admin_db.table("matches").update({
        "result_confirmed": True,
        "result_confirmed_at": datetime.utcnow().isoformat(),
        "status": "completed",
    }).eq("id", match_id).execute()

    advanced = False
    if match.get("next_match_id") and match.get("winner_id"):
        slot = match.get("next_match_slot") or "player2"
        admin_db.table("matches").update({
            f"{slot}_id": match["winner_id"],
            f"{slot}_name": match.get("winner_name"),
        }).eq("id", match["next_match_id"]).execute()
        advanced = True

    if notifications:
        admin_db.table("notifications").insert(notifications).execute()

    record_audit(
        admin_db,
        actor={"id": actor_id, "name": actor_name},
        action="match.confirm_result",
        entity_type="match",
        entity_id=match_id,
        previous_state=match,
        new_state={"result_confirmed": True, "status": "completed"},
        request_context={"advanced": advanced, "atomic": False},
    )

    return {"confirmed": True, "advanced": advanced, "already_confirmed": False,
            "match_id": match_id, "winner_id": match.get("winner_id")}
