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
    # fixture_published is reachable directly as well as through
    # fixture_generation. Once migration 002 is applied the CHECK on
    # tournaments.status accepts every name in this table, fixture_generation
    # included; on the original schema (draft, registration_open,
    # registration_closed, scheduled, ongoing, completed) it is written as its
    # legacy synonym by set_tournament_status below. Either way a draw can go
    # straight from a closed registration to a published bracket, which is the
    # path generate_fixtures takes.
    #
    # in_progress is reachable from registration_closed and fixture_generation
    # because a draw does not always move the status: fixtures generated while
    # the tournament was still a draft leave it there, and the organiser then
    # opens and closes registration around a bracket that already exists.
    # POST /tournaments/{id}/start is the only caller that takes these two
    # edges, and it refuses when the tournament has no matches, so a
    # tournament cannot be running without a draw to run.
    "registration_closed": {"fixture_generation", "fixture_published",
                            "in_progress", "registration_open", "cancelled"},
    "fixture_generation": {"fixture_published", "in_progress",
                           "registration_closed", "cancelled"},
    "fixture_published": {"in_progress", "fixture_generation", "cancelled"},
    "in_progress": {"completed", "fixture_published", "cancelled"},
    # Terminal to every lifecycle VERB: /start, /complete and /cancel all go
    # through validate_tournament_transition and are refused from here.
    #
    # There is exactly one way back, and it is not a transition an organiser
    # can ask for directly: reopening the match that DECIDED a completed
    # tournament returns it to in_progress and clears the recorded champion
    # (routers/matches.reopen_match). That path writes through
    # set_tournament_status, which persists without consulting this table, so
    # the correction works without opening the edge to anything else. Adding
    # {"in_progress"} here instead let POST /start un-complete a finished
    # tournament outright, which is how the lifecycle suite caught it.
    "completed": set(),
    "cancelled": set(),          # terminal, with no way back at all
}

MATCH_TRANSITIONS: Dict[str, Set[str]] = {
    "scheduled": {"ready", "live", "cancelled", "postponed"},
    "ready": {"live", "scheduled", "cancelled", "postponed"},
    "live": {"paused", "completed", "cancelled"},
    "paused": {"live", "completed", "cancelled"},
    # Terminal as far as this table is concerned. The correction workflow that
    # takes a match out of it is POST /matches/{id}/reopen, which deliberately
    # does not go through validate_match_transition: it undoes a confirmed
    # result under an owner's stated reason and audit trail, and is checked
    # against the bracket it fed rather than against this table.
    "completed": set(),
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


# The two states a tournament does not come back from. `cancelled` means the
# event is not happening; `completed` means it already has and a champion is
# recorded against it.
TERMINAL_TOURNAMENT_STATES = ("completed", "cancelled")


def assert_tournament_not_terminal(tournament: Dict, action: str) -> None:
    """
    Refuse an action that rewrites a tournament which is over.

    The lifecycle verbs guard themselves -- /start, /complete and /cancel all
    go through validate_tournament_transition -- but the routes that CHANGE A
    FINISHED TOURNAMENT'S CONTENTS never asked. Verified by probe: on a
    cancelled tournament an organiser could still force a redraw (deleting and
    rebuilding every match), publish the schedule (which fanned out "Match
    Schedule Published! Check your boards and timings" to all five
    participants of an event that is not happening), start a match, and add a
    fixture. The same was true of a completed one, where a redraw deletes the
    very matches the recorded champion was read from -- leaving a champion
    displayed beside an unplayed draw.

    Not applied to reads, to standings, or to the verbs themselves. This is for
    the writes that would make a finished tournament's record untrue.
    """
    status = canonical_tournament_status(tournament.get("status"))
    if status in TERMINAL_TOURNAMENT_STATES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This tournament is {status}, so it cannot {action}. "
                + ("Reopening a cancelled tournament is not supported; create a new one."
                   if status == "cancelled"
                   else "Its result is recorded and its matches are the evidence for it.")
            ),
        )


def assert_participants_can_be_added(tournament: Dict, force: bool = False) -> None:
    """
    Whether anyone may still be entered -- organisers included.

    `assert_tournament_accepts_registrations` above was applied to players
    only: an admin could enter participants at any stage, deliberately, so a
    late entry was possible after the desk had closed. But nothing narrowed
    that as the tournament moved on, so a participant could be added to a
    tournament whose draw was already generated and half played. They appear in
    the entry list and in nothing else -- no fixtures, no place in the table --
    and the only way to give them any is to regenerate the draw, which deletes
    every result in it.

    Closing registration is the moment the entry list becomes the thing the
    draw is made from, so that is where this stops. `force` is the organiser
    saying they know, and is audited by the routes that offer it; the app does
    not send it, so in the interface the option is simply gone.
    """
    if force:
        return
    status = canonical_tournament_status(tournament.get("status"))
    if status == "registration_open":
        return
    if status in ("draft", None):
        # Nothing has been announced yet; the entry list is still being built.
        return
    # The advice has to be advice the server will actually accept. Reopening
    # is only reachable from registration_closed and fixture_generation --
    # TOURNAMENT_TRANSITIONS gives fixture_published no edge back to
    # registration_open -- so offering it from a published draw sent
    # organisers to a button that answers 409.
    can_reopen = "registration_open" in TOURNAMENT_TRANSITIONS.get(status, set())
    if can_reopen:
        advice = ("Reopen registration to add someone, or add a match for them "
                  "from the Fixtures tab if the draw is already made.")
    elif status in TERMINAL_TOURNAMENT_STATES:
        advice = "This tournament is over; nothing further can be entered."
    else:
        advice = ("The draw is already published, so registration cannot be "
                  "reopened. Add a match for them from the Fixtures tab instead.")

    raise HTTPException(
        status_code=409,
        detail=(
            f"Registration is closed for this tournament (state: {status}), so no further "
            f"participants can be entered. {advice}"
        ),
    )


# Reverse of TOURNAMENT_ALIASES: the state name the *original* schema's CHECK
# constraint accepts, for databases where migration 002 has not been applied.
LEGACY_TOURNAMENT_NAMES: Dict[str, str] = {
    "fixture_published": "scheduled",
    "in_progress": "ongoing",
    "fixture_generation": "registration_closed",
}


# The migration that makes every name in TOURNAMENT_TRANSITIONS storable. 002
# widened the CHECK first; 012 re-asserts it (with 'cancelled', which the
# original schema never had) alongside the champion and cancellation columns,
# so it is the one to point an operator at whichever of the two they missed.
LIFECYCLE_MIGRATION = "db/migrations/012_lifecycle.sql"


def set_tournament_status(
    admin_db,
    tournament_id: str,
    target: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Persist a tournament status, tolerating a database that predates migration 002.

    The new state vocabulary (fixture_published, in_progress, ...) is only valid
    once that migration widens the CHECK constraint. Until then the closest
    legacy synonym is written instead, so fixture generation and stage
    transitions still work on an un-migrated database.

    `extra` is written in the same update as the status -- the champion of a
    completed tournament, the reason for a cancelled one -- so the row never
    shows a terminal state without the facts that go with it. Callers pass it
    only when they know the columns exist; this function does not probe.

    Returns the status actually written, or None if neither name was accepted.
    'completed' and 'cancelled' have no legacy synonym, so None there means the
    CHECK constraint itself is out of date and LIFECYCLE_MIGRATION is needed.
    """
    patch = dict(extra or {})
    patch["status"] = target
    try:
        admin_db.table("tournaments").update(patch).eq("id", tournament_id).execute()
        return target
    except Exception as e:
        fallback = LEGACY_TOURNAMENT_NAMES.get(target)
        if not fallback:
            logger.error(
                f"Could not set tournament {tournament_id} status to '{target}': {str(e)}. "
                f"Apply {LIFECYCLE_MIGRATION} if the status was rejected by the CHECK constraint."
            )
            return None
        logger.warning(
            f"Status '{target}' rejected by the database; writing legacy synonym "
            f"'{fallback}'. Apply {LIFECYCLE_MIGRATION} to enable the full state vocabulary."
        )
        try:
            patch["status"] = fallback
            admin_db.table("tournaments").update(patch).eq("id", tournament_id).execute()
            return fallback
        except Exception as inner:
            logger.error(f"Legacy status fallback also failed for {tournament_id}: {str(inner)}")
            return None
