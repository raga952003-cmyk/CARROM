"""
Tournament and match lifecycle rules (spec 75, 76).

State transitions are validated server-side; the frontend may request a change
but the backend decides whether it is legal. An invalid transition is rejected
rather than silently applied.
"""
from typing import Any, Dict, Optional, Set
from fastapi import HTTPException
import logging

logger = logging.getLogger("uvicorn.error")

# 'scheduled' and 'ongoing' are the original schema's names for
# 'fixture_published' and 'in_progress'. Both spellings are accepted so existing
# rows and the existing frontend keep working.
TOURNAMENT_ALIASES: Dict[str, str] = {
    "scheduled": "fixture_published",
    "ongoing": "in_progress",
}

TOURNAMENT_TRANSITIONS: Dict[str, Set[str]] = {
    "draft": {"registration_open", "cancelled"},
    "registration_open": {"registration_closed", "draft", "cancelled"},
    # fixture_published is reachable directly, because fixture_generation is not
    # a value the database will store: the CHECK on tournaments.status allows
    # draft, registration_open, registration_closed, scheduled, ongoing and
    # completed, and fixture_generation aliases to none of them. Routing through
    # it made the path from a closed registration to a running tournament
    # impassable, which is why no tournament has ever left registration_open.
    "registration_closed": {"fixture_generation", "fixture_published",
                            "registration_open", "cancelled"},
    "fixture_generation": {"fixture_published", "registration_closed", "cancelled"},
    "fixture_published": {"in_progress", "fixture_generation", "cancelled"},
    "in_progress": {"completed", "fixture_published", "cancelled"},
    "completed": set(),          # terminal
    "cancelled": set(),          # terminal
}

MATCH_TRANSITIONS: Dict[str, Set[str]] = {
    "scheduled": {"ready", "live", "cancelled", "postponed"},
    "ready": {"live", "scheduled", "cancelled", "postponed"},
    "live": {"paused", "completed", "cancelled"},
    "paused": {"live", "completed", "cancelled"},
    "completed": set(),          # only reachable again via a correction workflow
    "cancelled": {"scheduled"},
    "postponed": {"scheduled", "ready", "cancelled"},
}


def canonical_tournament_status(status: Optional[str]) -> Optional[str]:
    if status is None:
        return None
    return TOURNAMENT_ALIASES.get(status, status)


def _assert_transition(
    transitions: Dict[str, Set[str]],
    entity: str,
    current: str,
    target: str,
) -> None:
    if current == target:
        return  # re-asserting the current state is a no-op, not an error

    allowed = transitions.get(current)
    if allowed is None:
        raise HTTPException(
            status_code=422,
            detail=f"{entity} is in an unrecognised state '{current}'.",
        )
    if target not in transitions:
        raise HTTPException(
            status_code=422,
            detail=f"'{target}' is not a valid {entity.lower()} state.",
        )
    if target not in allowed:
        readable = ", ".join(sorted(allowed)) if allowed else "no further states"
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot move {entity.lower()} from '{current}' to '{target}'. "
                f"Allowed from '{current}': {readable}."
            ),
        )


def validate_tournament_transition(current: Optional[str], target: Optional[str]) -> None:
    """Raises 409/422 if the requested tournament state change is not permitted."""
    if target is None:
        return
    current_state = canonical_tournament_status(current) or "draft"
    target_state = canonical_tournament_status(target)
    _assert_transition(TOURNAMENT_TRANSITIONS, "Tournament", current_state, target_state)


def validate_match_transition(current: Optional[str], target: Optional[str]) -> None:
    """Raises 409/422 if the requested match state change is not permitted."""
    if target is None:
        return
    _assert_transition(MATCH_TRANSITIONS, "Match", current or "scheduled", target)


def assert_match_scorable(match: Dict) -> None:
    """
    A score may only be recorded against a match that is actually being played.
    Prevents back-dating a score onto a cancelled or already-confirmed match.
    """
    status = match.get("status")
    if match.get("result_confirmed"):
        raise HTTPException(
            status_code=409,
            detail="This match result is already confirmed. Use the correction workflow to change it.",
        )
    if status in ("cancelled", "postponed"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot score a match that is {status}.",
        )


def assert_tournament_accepts_registrations(tournament: Dict) -> None:
    status = canonical_tournament_status(tournament.get("status"))
    if status != "registration_open":
        raise HTTPException(
            status_code=409,
            detail=f"Registration is not open for this tournament (state: {status}).",
        )


# Reverse of TOURNAMENT_ALIASES: the state name the *original* schema's CHECK
# constraint accepts, for databases where migration 002 has not been applied.
LEGACY_TOURNAMENT_NAMES: Dict[str, str] = {
    "fixture_published": "scheduled",
    "in_progress": "ongoing",
    "fixture_generation": "registration_closed",
}


def set_tournament_status(admin_db, tournament_id: str, target: str) -> Optional[str]:
    """
    Persist a tournament status, tolerating a database that predates migration 002.

    The new state vocabulary (fixture_published, in_progress, ...) is only valid
    once that migration widens the CHECK constraint. Until then the closest
    legacy synonym is written instead, so fixture generation and stage
    transitions still work on an un-migrated database.

    Returns the status actually written, or None if neither name was accepted.
    """
    try:
        admin_db.table("tournaments").update({"status": target}).eq("id", tournament_id).execute()
        return target
    except Exception as e:
        fallback = LEGACY_TOURNAMENT_NAMES.get(target)
        if not fallback:
            logger.error(f"Could not set tournament {tournament_id} status to '{target}': {str(e)}")
            return None
        logger.warning(
            f"Status '{target}' rejected by the database; writing legacy synonym "
            f"'{fallback}'. Apply db/migrations/002_serverless_architecture.sql "
            f"to enable the full state vocabulary."
        )
        try:
            admin_db.table("tournaments").update({"status": fallback}).eq(
                "id", tournament_id
            ).execute()
            return fallback
        except Exception as inner:
            logger.error(f"Legacy status fallback also failed for {tournament_id}: {str(inner)}")
            return None
