from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_admin_db
from app.models.match import (
    ScoreSubmitSchema, BoardScoreSchema, TossSchema,
    MatchSidesSchema, WalkoverSchema, TieBreakSchema, MatchReopenSchema,
    MatchFixtureUpdateSchema,
)
from app.utils.security import verify_admin
from app.services.scoring_engine import (
    recalculate_match_scores, apply_queen_points, queen_award, board_result, scoring_mode,
    apply_set_results, summarise_sets, set_layout,
)
from app.services.notification_service import fan_out_notification, resolve_tournament_audience
from app.services.transaction_service import apply_board_result, confirm_match_result
from app.services.qualification import try_auto_promote, knockout_qualifiers_assigned, category_of
from app.services.access_control import require_tournament_access
from app.services.audit_service import record_audit
from app.services.state_machine import (
    validate_match_transition, assert_match_scorable,
    assert_tournament_not_terminal, canonical_tournament_status,
    set_tournament_status,
)
from app.services.score_validation import validate_board_score
from app.services.schedule_validation import detect_schedule_conflicts
from app.utils.serializers import serialize_board, serialize_match
from app.utils.idempotency import IdempotencyGuard, get_idempotency_key
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import logging
import time

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/matches", tags=["matches"])


def _assert_league_standings_mutable(admin_db, match: Dict[str, Any]) -> None:
    """Do not change league standings after their knockout seats are filled.

    Promotion currently replaces each rank placeholder with a participant
    name. Recomputing the table afterwards cannot identify the original seat
    and would leave the bracket showing the wrong qualifiers.
    """
    if match.get("stage") == "league" and knockout_qualifiers_assigned(
        admin_db, match["tournament_id"], category_of(match)
    ):
        raise HTTPException(status_code=409, detail=(
            "This league has already supplied knockout entrants. Its results and "
            "pairings are locked because changing the table would leave the "
            "knockout bracket with the wrong qualifiers."
        ))

_CLOSED_TO_PLAY = ("cancelled", "completed")


def _assert_tournament_accepts_play(tournament: Dict[str, Any]) -> None:
    """A cancelled or completed tournament is not still being played.

    Every one of these routes checked the MATCH -- its status, whether its
    result was confirmed, whether its league had already seeded a bracket --
    and none of them asked whether the TOURNAMENT was still running. So a
    cancelled event stayed fully scorable: boards submitted, results
    confirmed, the bracket advanced, and "Congratulations ... advancing to
    the next knockout round" went out to every entrant of an event that had
    just been called off.

    The same gap let a decided tournament be rewritten. record_walkover
    refuses when result_confirmed holds -- but a walkover never sets it, so a
    final settled BY walkover could be walked over again after the tournament
    closed, leaving the recorded champion disagreeing with the bracket with
    no route back.

    Reopening is deliberately not gated: reopen_match puts the tournament
    back to in_progress (matches.py:1568), and it is the way a finished
    tournament is legitimately opened up to correct something.
    """
    status = (tournament or {}).get("status")
    if status not in _CLOSED_TO_PLAY:
        return
    raise HTTPException(status_code=409, detail=(
        "This tournament was cancelled, so results can no longer be recorded."
        if status == "cancelled" else
        "This tournament is complete. Reopen the match you need to change "
        "first; that reopens the tournament with it."
    ))

def tournament_rules(admin_db, tournament_id: str) -> dict:
    """The tournament's scoring rules, for the queen value."""
    if not tournament_id:
        return {}
    rows = admin_db.table("tournaments").select("rules").eq(
        "id", tournament_id).execute().data
    return (rows[0].get("rules") or {}) if rows else {}


def _resolve_set(boards, board_number: int, requested):
    """
    Which set a board write means, or 422 when the request does not say.

    Board numbers restart at 1 in every set, so "board 1" of a three-set match
    names three different boards. A missing set used to fall back to 1, which
    turned a client that forgot to send one into a silent overwrite of a played
    result -- the umpire scoring set 2 rewrote set 1 and the set they were on
    never filled. Guessing is the wrong answer to an ambiguous request.
    """
    if requested is not None:
        return int(requested)
    same_number = [b for b in boards if b.get("board_number") == board_number]
    sets = {(b.get("set_number") or 1) for b in same_number}
    if len(sets) > 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "This match is played in sets, so board {} exists in each of "
                "them. Say which set the board belongs to.".format(board_number)
            ),
        )
    return next(iter(sets)) if sets else 1


def _official_game_points(boards, set_number: int, board_number: int, winner: str) -> int:
    """Winner's score entering a board, reset at the start of each game."""
    field = "player1_score" if winner == "player1" else "player2_score"
    return sum(int(b.get(field) or 0) for b in boards
               if (b.get("set_number") or 1) == set_number
               and b.get("board_number", 0) < board_number
               and b.get("status") == "completed")


def _validate_official_observation(
    winner, coins_with, coins, queen_by, covered_by,
    finish_type="normal", special_finish_extra_point=False,
    p1_penalty=0, p2_penalty=0,
):
    """ICF boards have one winner and credit only opposing men still on board."""
    if winner not in ("player1", "player2"):
        raise HTTPException(status_code=422, detail="Choose who finished and won this board.")
    if finish_type not in ("normal", "own_last_coin_queen_left"):
        raise HTTPException(status_code=422, detail="Choose a valid board finish type.")
    if finish_type == "own_last_coin_queen_left":
        if coins_with not in (None, "none") or coins != 0:
            raise HTTPException(status_code=422, detail=(
                "For the queen-left final-coin finish, record no opposing coins remaining."))
        if (queen_by or "none") != "none" or (covered_by or "none") != "none":
            raise HTTPException(status_code=422, detail=(
                "The queen must still be on the board for this special finish."))
        if p1_penalty or p2_penalty:
            raise HTTPException(status_code=422, detail=(
                "Record the demanded improper-stroke point using the special finish option."))
        return
    if special_finish_extra_point:
        raise HTTPException(status_code=422, detail=(
            "An extra point applies only to the queen-left improper-stroke finish."))
    opponent = "player2" if winner == "player1" else "player1"
    if coins_with != opponent:
        raise HTTPException(status_code=422, detail=(
            "The coins remaining must belong to the board winner's opponent."))
    if coins is None or not 0 <= coins <= 9:
        raise HTTPException(status_code=422, detail="Enter 0 to 9 opposing coins remaining.")
    queen_by = queen_by or "none"
    covered_by = covered_by or "none"
    if queen_by not in ("none", "player1", "player2") or covered_by not in (
            "none", "player1", "player2"):
        raise HTTPException(status_code=422, detail="Choose a valid side for the queen.")
    if covered_by != "none" and covered_by != queen_by:
        raise HTTPException(status_code=422, detail=(
            "The player who pockets the queen must cover it with their own coin."))


def _authorise_match(admin_db, match_id: str, admin, action: str):
    """Resolve a match to its tournament and authorise the caller for `action`."""
    match, _ = _authorise_match_with_tournament(admin_db, match_id, admin, action)
    return match


def _authorise_match_with_tournament(admin_db, match_id: str, admin, action: str):
    """
    The same, handing back the tournament row it already had to read.

    Authorising a match loads its tournament to find the owner. Every scoring
    route then asked for the same row a second time, for the rules -- so every
    board an umpire entered spent a round trip re-reading a row this request
    was already holding. On a venue connection that is the difference the
    umpire feels between one tap and the next.
    """
    rows = admin_db.table("matches").select("*").eq("id", match_id).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Match not found.")
    match = rows[0]
    tournament = require_tournament_access(admin_db, match["tournament_id"], admin, action)
    return match, tournament




# Columns added by migration 005. Until it is applied the board detail has
# nowhere to go, but the SCORES are still correct — so the detail is dropped
# and the board is recorded rather than the umpire being blocked mid-match.
_BOARD_DETAIL_COLUMNS = (
    "board_winner", "p1_coins_pocketed", "p2_coins_pocketed",
    "coins_remaining_with", "coins_remaining", "queen_pocketed_by",
    "queen_covered_by", "queen_status", "queen_awarded_to",
    "p1_penalty", "p2_penalty", "base_points", "queen_bonus",
    "scoring_warnings", "locked", "confirmed_by", "confirmed_at",
)
_board_detail_available: Dict[str, Any] = {}
_PROBE_RETRY_SECONDS = 30


def board_detail_available(admin_db) -> bool:
    """
    Whether migration 005 has been applied.

    A negative answer is re-checked, because caching it for the life of the
    process meant applying the migration changed nothing until a restart.
    """
    cached = _board_detail_available.get("value")
    if cached is True:
        return True
    if cached is False and time.monotonic() - _board_detail_available.get("at", 0) < _PROBE_RETRY_SECONDS:
        return False
    try:
        admin_db.table("boards").select("board_winner").limit(1).execute()
        _board_detail_available["value"] = True
    except Exception:
        _board_detail_available["value"] = False
        _board_detail_available["at"] = time.monotonic()
    return _board_detail_available["value"]


_walkover_available: Dict[str, Any] = {}


_WALKOVER_COLUMNS = ("walkover", "walkover_reason", "walkover_by")


def walkover_columns(admin_db) -> tuple:
    """
    Which of the walkover columns this database actually has.

    Probed one at a time rather than as a set, because some databases already
    carry `walkover` and `walkover_reason` from before these were tracked in
    schema.sql while lacking `walkover_by`. Probing any single column would
    then either report success and let the write fail, or report failure and
    throw away two columns that were there all along.
    """
    cached = _walkover_available.get("value")
    if cached is not None and (
        cached == _WALKOVER_COLUMNS
        or time.monotonic() - _walkover_available.get("at", 0) < _PROBE_RETRY_SECONDS
    ):
        return cached

    present = []
    for column in _WALKOVER_COLUMNS:
        try:
            admin_db.table("matches").select(column).limit(1).execute()
            present.append(column)
        except Exception:
            pass
    found = tuple(present)
    _walkover_available["value"] = found
    _walkover_available["at"] = time.monotonic()
    return found


@router.post("/{id}/start")
async def start_match(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        current, start_tournament = _authorise_match_with_tournament(
            admin_db, id, admin, "match.start")
        _assert_tournament_accepts_play(start_tournament)
        # A cancelled event is not being played, and a completed one already
        # has been -- starting a match in either makes the tournament's own
        # record untrue. Checked on start rather than on every scoring call:
        # a match that cannot begin cannot be scored.
        assert_tournament_not_terminal(start_tournament, "have a match started in it")
        validate_match_transition(current.get("status"), "live")

        # Already running: leave the clock alone.
        #
        # validate_match_transition treats live->live as a no-op rather than
        # an error, so a second press fell through to the update below and
        # reset timer_started_at to now. The elapsed time is measured FROM
        # that stamp, so a match twenty minutes in went back to zero. Probed:
        # 1,200,007 ms lost, HTTP 200, nothing said.
        #
        # This is not a rare double-click. The scorer's Start button shows
        # whenever their copy of the row still reads 'scheduled', so any stale
        # tab, second device or umpire returning to the match screen is one
        # tap from wiping the clock of a match in progress.
        if current.get("status") == "live" and current.get("is_timer_running"):
            return serialize_match(current)

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        res = admin_db.table("matches").update({
            "status": "live",
            "timer_started_at": now_ms,
            "is_timer_running": True
        }).eq("id", id).execute()
        return serialize_match(res.data[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{id}/pause")
async def pause_match(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        # Fetch current match
        m = _authorise_match(admin_db, id, admin, "match.pause")
        validate_match_transition(m.get("status"), "paused")
        elapsed = m.get("timer_elapsed_seconds", 0)
        started_at = m.get("timer_started_at")
        
        if m.get("is_timer_running") and started_at:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            elapsed += int((now_ms - started_at) / 1000)

        res = admin_db.table("matches").update({
            "status": "paused",
            "is_timer_running": False,
            "timer_elapsed_seconds": elapsed
        }).eq("id", id).execute()
        return serialize_match(res.data[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{id}/resume")
async def resume_match(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        current = _authorise_match(admin_db, id, admin, "match.resume")
        validate_match_transition(current.get("status"), "live")

        # Resuming a match that was never paused is the same no-op, for the
        # same reason: it would restart the clock from now.
        if current.get("status") == "live" and current.get("is_timer_running"):
            return serialize_match(current)

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        res = admin_db.table("matches").update({
            "status": "live",
            "timer_started_at": now_ms,
            "is_timer_running": True
        }).eq("id", id).execute()
        return serialize_match(res.data[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{id}/boards")
async def add_board(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        match, tournament = _authorise_match_with_tournament(
            admin_db, id, admin, "match.add_board")
        _assert_tournament_accepts_play(tournament)
        assert_tournament_not_terminal(tournament, "change its boards")
        if match.get("result_confirmed"):
            raise HTTPException(status_code=409, detail="Reopen this result before changing its boards.")
        rules = (tournament or {}).get("rules") or {}
        if rules.get("setWinnerRule") == "target_points":
            boards = admin_db.table("boards").select("*").eq("match_id", id).execute().data or []
            tied = next((row for row in summarise_sets(match, boards, rules)
                         if row["needsExtraBoard"]), None)
            if tied is None:
                raise HTTPException(status_code=409,
                                    detail="A deciding board can be added only after a game ends level.")
            set_number = tied["setNumber"]
            number = max(b["board_number"] for b in boards
                         if (b.get("set_number") or 1) == set_number) + 1
            row = admin_db.table("boards").insert({
                "match_id": id, "set_number": set_number,
                "board_number": number, "status": "in_progress",
                "player1_score": 0, "player2_score": 0,
            }).execute().data[0]
            admin_db.table("matches").update({
                "tie_break_required": False,
            }).eq("id", id).execute()
            return serialize_board(row)

        if set_layout(match, rules)[0] > 1:
            raise HTTPException(status_code=409, detail=(
                "A multi-game match can add a board only to decide a tied game."))
        if match.get("status") == "completed":
            raise HTTPException(status_code=409, detail=(
                "This match is complete. Reopen or correct its result before adding boards."))

        # Count existing boards
        boards_res = admin_db.table("boards").select("board_number").eq("match_id", id).execute()
        count = len(boards_res.data)
        
        # Insert a new board
        board_payload = {
            "match_id": id,
            "board_number": count + 1,
            "status": "pending",
            "player1_score": 0,
            "player2_score": 0
        }
        res = admin_db.table("boards").insert(board_payload).execute()

        # max_boards is deliberately NOT raised here.
        #
        # It is the configured length of the match, and the win condition is a
        # majority of it. Raising it on every added board meant each extra board
        # moved the finish line further away: a match with 8 boards clicked up
        # to 28 needed 15 board wins instead of 5, and under remaining-coins
        # scoring -- which requires every board to be played -- it could never
        # be completed at all. An extra board is a tie-break board, not a longer
        # match.
        return serialize_board(res.data[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{id}/boards/resize")
async def resize_match_boards(id: str, boards: int = Query(..., ge=1, le=31),
                              admin = Depends(verify_admin)):
    """
    Set how many boards this match is played over.

    Fixtures already generated carry the length they were generated with, so
    changing the tournament rules afterwards does not reach them — and
    regenerating the draw would throw away every board already played. This
    changes one match in place.

    Boards are added or trailing unplayed ones removed until the count matches,
    and max_boards moves with it so the win condition stays consistent with the
    match actually being played. It will not go below the boards already
    played: shortening a match to less than has happened would silently discard
    real results.
    """
    admin_db = get_admin_db()
    try:
        match, tournament = _authorise_match_with_tournament(
            admin_db, id, admin, "match.add_board")
        _assert_tournament_accepts_play(tournament)
        assert_tournament_not_terminal(tournament, "resize its boards")
        if match.get("result_confirmed") or match.get("status") == "completed":
            raise HTTPException(status_code=409, detail=(
                "A completed match cannot have its board limit changed."))
        if set_layout(match, (tournament or {}).get("rules") or {})[0] > 1:
            raise HTTPException(status_code=409, detail=(
                "Resize each game through the tournament rules before fixtures are played."))

        existing = admin_db.table("boards").select("*").eq(
            "match_id", id).order("board_number").execute().data or []
        played = [b for b in existing if b.get("status") == "completed"
                  or (b.get("player1_score") or 0) or (b.get("player2_score") or 0)]

        if played:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This match already has a board result. Changing the board limit now "
                    "would change its win condition."
                ),
            )

        added = removed = 0
        if boards > len(existing):
            rows = [{
                "match_id": id,
                "board_number": n,
                "status": "pending",
                "player1_score": 0,
                "player2_score": 0,
            } for n in range(len(existing) + 1, boards + 1)]
            admin_db.table("boards").insert(rows).execute()
            added = len(rows)
        elif boards < len(existing):
            # Trailing first, so numbering stays contiguous.
            for b in reversed(existing[boards:]):
                admin_db.table("boards").delete().eq("id", b["id"]).execute()
                removed += 1

        admin_db.table("matches").update({"max_boards": boards}).eq("id", id).execute()

        record_audit(
            admin_db, actor=admin, action="match.resize_boards",
            entity_type="match", entity_id=id,
            previous_state={"boards": len(existing)},
            new_state={"boards": boards, "added": added, "removed": removed},
        )
        return {
            "status": "success",
            "boards": boards,
            "added": added,
            "removed": removed,
            "message": "This match is now played over {} board(s).".format(boards),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{id}/boards/unplayed")
async def remove_unplayed_boards(id: str, admin = Depends(verify_admin)):
    """
    Drop trailing boards that were never played.

    Add Board is one click and there is nothing to undo it, so a match can end
    up carrying boards nobody intends to play. Under remaining-coins scoring
    that is not cosmetic: the match completes only when EVERY board is
    completed, so a handful of stray rows leaves it permanently undecided and
    the result impossible to confirm.

    Only trailing boards go, so the numbering stays contiguous, and only ones
    with no play on them: nothing completed, nothing scored, nothing locked. A
    match is left with at least one board.
    """
    admin_db = get_admin_db()
    try:
        match, tournament = _authorise_match_with_tournament(
            admin_db, id, admin, "match.add_board")
        _assert_tournament_accepts_play(tournament)
        assert_tournament_not_terminal(tournament, "remove its boards")
        if match.get("result_confirmed"):
            raise HTTPException(status_code=409, detail="Reopen this result before removing boards.")
        rules = (tournament or {}).get("rules") or {}
        if set_layout(match, rules)[0] > 1 or rules.get("setWinnerRule") == "target_points":
            raise HTTPException(status_code=409, detail=(
                "Configured games keep their board limit. Use a deciding board for a tie."))

        boards = admin_db.table("boards").select("*").eq(
            "match_id", id).order("board_number").execute().data or []
        if not boards:
            raise HTTPException(status_code=404, detail="This match has no boards.")

        def untouched(b) -> bool:
            return (
                b.get("status") != "completed"
                and not b.get("locked")
                and not (b.get("player1_score") or 0)
                and not (b.get("player2_score") or 0)
                and (b.get("board_winner") or "none") == "none"
            )

        # Walk back from the end and stop at the first board with play on it.
        removable = []
        for b in reversed(boards):
            if len(boards) - len(removable) <= 1 or not untouched(b):
                break
            removable.append(b)

        if not removable:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Nothing to remove: the last board has been played, or only "
                    "one board is left."
                ),
            )

        for b in removable:
            admin_db.table("boards").delete().eq("id", b["id"]).execute()

        remaining = len(boards) - len(removable)
        # Bring the win condition and the result back into agreement. Under
        # remaining-coins scoring an extra pending board prevented completion;
        # simply deleting it left the row live with no winner even though all
        # remaining boards had been played.
        kept = [b for b in boards if b not in removable]
        baseline = {**match, "max_boards": remaining}
        if match.get("status") == "completed":
            baseline["status"] = "live"
        decided = recalculate_match_scores(baseline, kept, rules)
        patch = {
            "max_boards": remaining,
            "player1_board_wins": decided["player1BoardWins"],
            "player2_board_wins": decided["player2BoardWins"],
            "player1_total_points": decided["player1TotalPoints"],
            "player2_total_points": decided["player2TotalPoints"],
            "winner_id": decided["winnerId"],
            "winner_name": decided["winnerName"],
            "status": decided["status"],
            "match_completed_at": decided.get("matchCompletedAt"),
        }
        if board_detail_available(admin_db):
            patch["tie_break_required"] = decided.get("tieBreakRequired", False)
            patch["tie_break_rule"] = decided.get("tieBreakRule")
        admin_db.table("matches").update(patch).eq("id", id).execute()

        record_audit(
            admin_db, actor=admin, action="match.remove_unplayed_boards",
            entity_type="match", entity_id=id,
            previous_state={"boards": len(boards)},
            new_state={"boards": remaining, "removed": len(removable)},
        )
        return {
            "status": "success",
            "removed": len(removable),
            "boardsRemaining": remaining,
            "message": "Removed {} unplayed board(s); this match is now {} board(s).".format(
                len(removable), remaining),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{id}/boards/{board_number}")
async def update_board(
    id: str,
    board_number: int,
    data: BoardScoreSchema,
    reason: str = Query("Scorer update"),
    override: bool = Query(False, description="Required to change a confirmed board."),
    admin = Depends(verify_admin),
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Correct a board that has already been scored.

    A correction is a second scoring of the same board, so it is held to the
    same rules as the first: the numbers that will be stored are validated,
    and the board row, the audit row and the recomputed match aggregates are
    written together through apply_board_result. Before this they were three
    separate writes, and a connection dropped between them left a board
    showing one score while its match totalled another.
    """
    admin_db = get_admin_db()
    # The query string is part of the request: the same key sent again with a
    # different reason or override flag is a different correction, not a retry.
    guard = IdempotencyGuard(
        admin_db, idempotency_key,
        f"PUT /matches/{id}/boards/{board_number}",
        {**data.model_dump(), "reason": reason, "override": override},
    )
    cached = guard.replay()
    if cached is not None:
        return cached

    try:
        # Correcting a board is scoring, and was the one scoring path that never
        # checked. Being an admin was enough to rewrite any board on anyone's
        # tournament, which is precisely what the ownership model exists to stop.
        match_data, tournament_row = _authorise_match_with_tournament(
            admin_db, id, admin, "match.score")
        _assert_tournament_accepts_play(tournament_row)
        _assert_league_standings_mutable(admin_db, match_data)

        if match_data.get("result_confirmed"):
            # A confirmed result is the official record, and it may already
            # have sent a winner into the next round. Editing a board underneath
            # it would leave the totals disagreeing with a result that still
            # stands, so the result has to be taken back first.
            raise HTTPException(
                status_code=409,
                detail=(
                    "This match result is confirmed. Reopen the result first, "
                    "then correct the board."
                ),
            )

        boards = admin_db.table("boards").select("*").eq(
            "match_id", id).order("board_number").execute().data or []

        # Board numbers restart in each set, so a match played in sets has
        # several boards with this number. A correction that names the set gets
        # that board; one that does not gets the only board of that number, or
        # the first set's -- which is the board every match without sets has.
        same_number = [b for b in boards if b.get("board_number") == board_number]
        wanted = _resolve_set(boards, board_number, data.set_number)
        pb = next((b for b in same_number
                   if (b.get("set_number") or 1) == wanted), None)
        if pb is None:
            raise HTTPException(status_code=404, detail="Board not found")
        if data.status not in ("pending", "in_progress", "completed"):
            raise HTTPException(status_code=422, detail="Invalid board status.")

        if pb.get("locked") and not override:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Board {} is confirmed. Re-submitting would quietly rewrite a played "
                    "game, so an override and a reason are required to change it."
                ).format(board_number),
            )
        overriding = bool(pb.get("locked") and override)

        # Corrections go through the same rule as the original submission, so a
        # fixed score is not left missing the queen — or, under remaining-coins
        # scoring, silently re-scored under the classic formula.
        corrected_rules = ((tournament_row or {}).get("rules") or {})
        corrected_mode = scoring_mode(corrected_rules)
        if corrected_mode != "official_icf" and (
                data.finish_type not in (None, "normal")
                or data.special_finish_extra_point):
            raise HTTPException(status_code=422, detail=(
                "Special board finishes are available only under federation scoring."))
        if corrected_mode == "official_icf":
            if not board_detail_available(admin_db):
                raise HTTPException(status_code=503, detail=(
                    "Official carrom scoring needs board detail migration 005."))
            if data.status == "completed" and any(
                b.get("status") == "completed"
                and (b.get("set_number") or 1) == wanted
                and b.get("board_number", 0) > board_number
                for b in boards
            ):
                raise HTTPException(status_code=409, detail=(
                    "Correct the latest played board first. A score earlier in this game "
                    "can change whether later queen bonuses are legal."))

        if corrected_mode in ("remaining_coins", "official_icf"):
            # A correction restates the observations, so it is scored from them
            # rather than from the two numbers, which are outputs not inputs.
            # Anything the correction does not mention keeps what the board
            # already had; `is None` rather than `or`, so a deliberate 0 sticks.
            def restated(field, stored, fallback=None):
                value = getattr(data, field, None)
                if value is not None:
                    return value
                return pb.get(stored, fallback) if pb.get(stored) is not None else fallback

            if corrected_mode == "official_icf" and data.status == "completed":
                _validate_official_observation(
                    restated("board_winner", "board_winner", "none"),
                    restated("coins_remaining_with", "coins_remaining_with"),
                    restated("coins_remaining", "coins_remaining"),
                    restated("queen_pocketed_by", "queen_pocketed_by") or data.queen_claimed_by,
                    restated("queen_covered_by", "queen_covered_by"),
                    restated("finish_type", "finish_type", "normal"),
                    restated("special_finish_extra_point", "special_finish_extra_point", False),
                    restated("p1_penalty", "p1_penalty", 0),
                    restated("p2_penalty", "p2_penalty", 0),
                )
            outcome = board_result(
                winner=restated("board_winner", "board_winner", "none"),
                p1_coins_pocketed=restated("p1_coins_pocketed", "p1_coins_pocketed"),
                p2_coins_pocketed=restated("p2_coins_pocketed", "p2_coins_pocketed"),
                coins_remaining_with=restated("coins_remaining_with", "coins_remaining_with"),
                coins_remaining=restated("coins_remaining", "coins_remaining"),
                queen_pocketed_by=restated("queen_pocketed_by", "queen_pocketed_by")
                                  or data.queen_claimed_by,
                queen_covered_by=restated("queen_covered_by", "queen_covered_by"),
                finish_type=restated("finish_type", "finish_type", "normal"),
                special_finish_extra_point=restated(
                    "special_finish_extra_point", "special_finish_extra_point", False),
                p1_penalty=restated("p1_penalty", "p1_penalty", 0) or 0,
                p2_penalty=restated("p2_penalty", "p2_penalty", 0) or 0,
                game_points_before=(
                    _official_game_points(boards, wanted, board_number,
                                          restated("board_winner", "board_winner", "none"))
                    if corrected_mode == "official_icf" else 0),
                rules=corrected_rules,
            )
            c_p1, c_p2 = outcome["player1_score"], outcome["player2_score"]

            # Validate what is about to be STORED. The two typed numbers are
            # not read under this mode, so checking them would check nothing;
            # the score derived from the observations is what has to meet the
            # ceiling, exactly as submit_board checks its own derived score.
            validate_board_score(
                c_p1, c_p2, match_data, data.queen_claimed_by,
                allow_scoreless_queen=True,
            )
        else:
            # Judged on the coin count before the queen is added, as the
            # original submission was, so a board won with the queen is not
            # read as both sides reaching the target.
            validate_board_score(
                data.player1_score, data.player2_score, match_data,
                data.queen_claimed_by,
            )
            # The classic board STORES the queen-inclusive score, and a
            # correction restates what is on the screen -- which is that stored
            # total, not the coin count the original submission took. Applying
            # the award to it again added the queen a second time, and a third,
            # and a fourth: a board won 21-3 with the queen was stored 24-3,
            # then 27-3 on the first correction, 30-3 on the next, with nothing
            # refusing it. The ceiling is 60 and both-reached-target needs 29
            # each, so it climbed in silence. Board wins stayed right, because
            # the winner is declared; it is the POINTS that rotted, and points
            # are the league's tie-break.
            #
            # So take back the award the board is already carrying, then apply
            # the one the correction states. Restating a board unchanged is now
            # what it looks like: no change.
            had1, had2, _ = queen_award(
                pb.get("queen_claimed_by"), pb.get("queen_covered"), corrected_rules)

            # Floored at zero before the new award goes on.
            #
            # Taking the old queen back is a subtraction, and a correction that
            # moves the queen to the other player subtracts it from somebody
            # whose remaining coins are worth less than the bonus. Probed: a
            # board stored 23-5 (20 coins plus a covered queen), corrected to
            # "Ann scored 0, the queen was Ben's", stored -3 for Ann and took
            # her match total to -3. A negative board is not a thing in carrom,
            # and the figure feeds straight into net score difference, which is
            # the league's tie-break.
            #
            # Zero is the right floor rather than an error: the remaining-coins
            # path already floors at zero for the same reason
            # (scoring_engine.board_result), so the two modes now agree.
            base1 = max(0, data.player1_score - had1)
            base2 = max(0, data.player2_score - had2)
            c_p1, c_p2, _ = apply_queen_points(
                base1, base2,
                data.queen_claimed_by, data.queen_covered, corrected_rules,
            )

            # Validate what is about to be STORED, not only what was typed.
            # validate_board_score above checked data.player1_score; the queen
            # is added after it, so a correction could store a total above the
            # ceiling that the original submission would have been refused for.
            validate_board_score(
                c_p1, c_p2, match_data, data.queen_claimed_by,
                allow_scoreless_queen=True,
            )

        board_patch = {
            "player1_score": c_p1,
            "player2_score": c_p2,
            "status": data.status,
            "board_winner": (data.board_winner if data.board_winner is not None
                             else pb.get("board_winner") or "none"),
            "queen_claimed_by": data.queen_claimed_by,
            "queen_covered": data.queen_covered,
            "fouls_player1": data.fouls_player1,
            "fouls_player2": data.fouls_player2,
            "white_coins_pocketed": data.white_coins_pocketed,
            "black_coins_pocketed": data.black_coins_pocketed,
            "notes": data.notes
        }

        if corrected_mode in ("remaining_coins", "official_icf"):
            # Store what the correction observed, not just what it scored, so a
            # second correction reads the current board rather than the original.
            board_patch.update({
                "board_winner": outcome["board_winner"],
                "coins_remaining_with": restated("coins_remaining_with", "coins_remaining_with"),
                "coins_remaining": restated("coins_remaining", "coins_remaining"),
                "p1_coins_pocketed": restated("p1_coins_pocketed", "p1_coins_pocketed"),
                "p2_coins_pocketed": restated("p2_coins_pocketed", "p2_coins_pocketed"),
                "queen_pocketed_by": restated("queen_pocketed_by", "queen_pocketed_by"),
                "queen_covered_by": restated("queen_covered_by", "queen_covered_by"),
                "queen_status": outcome["queen_status"],
                "queen_awarded_to": outcome["queen_awarded_to"],
                "base_points": outcome["base_points"],
                "queen_bonus": outcome["queen_bonus"],
                "p1_penalty": restated("p1_penalty", "p1_penalty", 0) or 0,
                "p2_penalty": restated("p2_penalty", "p2_penalty", 0) or 0,
                "scoring_warnings": outcome["warnings"] or None,
            })
            if corrected_mode == "official_icf":
                board_patch.update({
                    "finish_type": restated("finish_type", "finish_type", "normal"),
                    "special_finish_extra_point": restated(
                        "special_finish_extra_point", "special_finish_extra_point", False),
                })

        if corrected_mode == "official_icf" and data.status != "completed":
            # To correct an earlier game score, first roll back later boards
            # from latest to earliest. Clear their lock and observations so
            # they can be played back in order with the right queen threshold.
            c_p1 = c_p2 = 0
            board_patch.update({
                "player1_score": 0, "player2_score": 0,
                "board_winner": "none", "queen_claimed_by": "none",
                "queen_covered": False, "coins_remaining_with": None,
                "coins_remaining": None, "queen_pocketed_by": "none",
                "queen_covered_by": "none", "queen_status": None,
                "queen_awarded_to": "none", "base_points": 0,
                "queen_bonus": 0, "p1_penalty": 0, "p2_penalty": 0,
                "scoring_warnings": None, "locked": False,
                "confirmed_by": None, "confirmed_at": None,
                "completed_at": None,
            })

        detail_available = board_detail_available(admin_db)
        if not detail_available:
            for key in _BOARD_DETAIL_COLUMNS:
                board_patch.pop(key, None)
        if data.status == "completed":
            board_patch["completed_at"] = datetime.utcnow().isoformat()

        # Recompute the match from the board set as it will be after this
        # write, with the engine the submission used, so a correction in a
        # match played in sets is decided the way its boards were.
        projected = [{**b, **board_patch} if b is pb else b for b in boards]
        total_sets, _ = set_layout(match_data, corrected_rules)

        # The engine writes a status only when it decides the match; otherwise
        # the row's own status comes back out. At submission that row says
        # 'live' and the answer is right. At correction it usually says
        # 'completed', because the match was -- so a correction that took the
        # deciding board away left status='completed' with winner_id NULL: a
        # finished match nobody won. Recomputing from 'live' makes the engine
        # earn 'completed' again. A paused match is left paused.
        baseline = ({**match_data, "status": "live"}
                    if match_data.get("status") == "completed" else match_data)
        updated_match = (apply_set_results(baseline, projected, corrected_rules)
                         if total_sets > 1 or corrected_rules.get("setWinnerRule") == "target_points"
                         else recalculate_match_scores(baseline, projected, corrected_rules))

        match_patch = {
            "player1_board_wins": updated_match["player1BoardWins"],
            "player2_board_wins": updated_match["player2BoardWins"],
            "player1_total_points": updated_match["player1TotalPoints"],
            "player2_total_points": updated_match["player2TotalPoints"],
            "status": updated_match["status"],
            "winner_id": updated_match["winnerId"],
            "winner_name": updated_match["winnerName"],
            "match_completed_at": updated_match.get("matchCompletedAt"),
        }

        # A correction that levels the scores leaves the engine with no winner
        # and tie_break_required set. Writing only the fields above kept
        # status='completed' with winner_id NULL, and the standings read a match
        # with no winner as a DRAW -- so correcting one board quietly turned a
        # decided match into a drawn one, awarded both sides the draw points,
        # and left nothing anywhere saying a decision was still owed.
        #
        # Both scoring models raise the flag now -- a classic knockout drawn on
        # board wins as much as a remaining-coins match level on points -- so
        # it is persisted for both, as submit_board does.
        if "tieBreakRequired" in updated_match:
            needs_tie_break = bool(updated_match.get("tieBreakRequired"))
            match_patch["tie_break_required"] = needs_tie_break
            match_patch["tie_break_rule"] = updated_match.get("tieBreakRule")
            if needs_tie_break:
                # Not finished: it is waiting on a ruling, and saying so is the
                # difference between a match an organiser can act on and one
                # that silently reads as a draw.
                match_patch["status"] = "live"
                match_patch["match_completed_at"] = None
        if total_sets > 1:
            match_patch["player1_sets_won"] = updated_match.get("player1SetsWon", 0)
            match_patch["player2_sets_won"] = updated_match.get("player2SetsWon", 0)
        if not detail_available:
            # Migration 005 carries the tie-break columns; without it the match
            # still reopens, it just cannot say a ruling is owed.
            match_patch.pop("tie_break_required", None)
            match_patch.pop("tie_break_rule", None)

        board_row = apply_board_result(
            admin_db,
            match_id=id,
            board_number=board_number,
            board_patch=board_patch,
            match_patch=match_patch,
            audit={
                "admin_id": admin["id"],
                "admin_name": admin["name"],
                "new_score": {"player1": c_p1, "player2": c_p2},
                "reason": ("OVERRIDE of a confirmed board: " + reason) if overriding else reason,
            },
            # A correction never opens the next board: the submission did that
            # when the board was first played.
            next_board_number=None,
            set_number=pb.get("set_number"),
        )

        response = serialize_board(board_row) if board_row else {"status": "ok"}
        guard.store(response)
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{id}/boards/{board_number}/submit")
async def submit_board(
    id: str,
    board_number: int,
    data: ScoreSubmitSchema,
    admin = Depends(verify_admin),
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Record a finished board (spec 70, 73).

    The score is validated and the result computed server-side; all resulting
    writes are applied in one transaction (spec 71).
    """
    admin_db = get_admin_db()
    guard = IdempotencyGuard(
        admin_db, idempotency_key,
        f"POST /matches/{id}/boards/{board_number}/submit",
        data.model_dump(),
    )
    cached = guard.replay()
    if cached is not None:
        return cached

    try:
        match_data, tournament_row = _authorise_match_with_tournament(
            admin_db, id, admin, "match.score")
        _assert_tournament_accepts_play(tournament_row)
        _assert_league_standings_mutable(admin_db, match_data)
        assert_match_scorable(match_data)
        if match_data.get("status") == "completed":
            raise HTTPException(status_code=409, detail=(
                "This match is complete. Correct a board or reopen the result instead."))

        boards = admin_db.table("boards").select("*").eq("match_id", id).order("board_number").execute().data or []

        # Board numbers restart in each set, so the set has to be named or the
        # board is ambiguous. Unset is only allowed when it cannot be.
        set_number = _resolve_set(boards, board_number, data.set_number)
        def is_target(b):
            return (b["board_number"] == board_number
                    and (b.get("set_number") or 1) == set_number)

        if not any(is_target(b) for b in boards):
            raise HTTPException(
                status_code=404,
                detail="Board {} of set {} does not exist on this match.".format(
                    board_number, set_number),
            )

        target_board = next(b for b in boards if is_target(b))
        if target_board.get("status") == "completed" or target_board.get("locked"):
            raise HTTPException(status_code=409, detail=(
                "This board has already been scored. Use the board correction action with a reason."))

        rules = ((tournament_row or {}).get("rules") or {})
        mode = scoring_mode(rules)
        if mode != "official_icf" and (
                data.finish_type != "normal" or data.special_finish_extra_point):
            raise HTTPException(status_code=422, detail=(
                "Special board finishes are available only under federation scoring."))
        if mode == "official_icf" and not board_detail_available(admin_db):
            raise HTTPException(status_code=503, detail=(
                "Official carrom scoring needs board detail migration 005."))
        official_game = rules.get("setWinnerRule") == "target_points"
        if official_game:
            games = summarise_sets(match_data, boards, rules)
            active = next((game for game in games if game["status"] != "completed"), None)
            if active is None or set_number != active["setNumber"]:
                raise HTTPException(status_code=409,
                                    detail="Play the current game before scoring another one.")
            if active["needsExtraBoard"]:
                raise HTTPException(status_code=409,
                                    detail="Add a deciding board for this tied game.")
            first_open = next((b for b in sorted(
                (b for b in boards if (b.get("set_number") or 1) == set_number),
                key=lambda b: b["board_number"])
                if b.get("status") != "completed"), None)
            if first_open is None or first_open["board_number"] != board_number:
                raise HTTPException(status_code=409,
                                    detail="Score the next unplayed board in this game.")

        validate_board_score(
            data.p1_score, data.p2_score, match_data, data.queen_claimed_by,
            allow_scoreless_queen=(mode in ("remaining_coins", "official_icf")),
        )

        if mode in ("remaining_coins", "official_icf"):
            # The umpire's observations are scored server-side. Each one is
            # taken as given: the winner, the queen and the coins left on the
            # board are three separate facts and none is inferred from another.
            if mode == "official_icf":
                _validate_official_observation(
                    data.board_winner, data.coins_remaining_with, data.coins_remaining,
                    data.queen_pocketed_by or data.queen_claimed_by,
                    data.queen_covered_by,
                    data.finish_type, data.special_finish_extra_point,
                    data.p1_penalty, data.p2_penalty,
                )
            outcome = board_result(
                winner=data.board_winner or "none",
                # Passed through as-is: the engine treats None as "not counted"
                # and skips the cross-check. Coercing it to 0 here made every
                # board look like nobody had pocketed anything, so the check
                # compared against a full board and warned on every entry.
                p1_coins_pocketed=data.p1_coins_pocketed,
                p2_coins_pocketed=data.p2_coins_pocketed,
                coins_remaining_with=data.coins_remaining_with,
                coins_remaining=data.coins_remaining,
                queen_pocketed_by=data.queen_pocketed_by or data.queen_claimed_by,
                queen_covered_by=data.queen_covered_by,
                finish_type=data.finish_type,
                special_finish_extra_point=data.special_finish_extra_point,
                p1_penalty=data.p1_penalty or 0,
                p2_penalty=data.p2_penalty or 0,
                game_points_before=(
                    _official_game_points(boards, set_number, board_number, data.board_winner)
                    if mode == "official_icf" else 0),
                rules=rules,
            )
            p1_final = outcome["player1_score"]
            p2_final = outcome["player2_score"]
            queen_note = (
                f"base {outcome['base_points']} + queen {outcome['queen_bonus']} "
                f"to {outcome['queen_awarded_to']}"
            )

            # Validate what is about to be STORED, not only what was typed.
            #
            # Under this mode the scorer enters observations -- who won, who
            # still held coins, how many -- and the score is derived from them
            # afterwards. The validate_board_score() call above therefore
            # checked numbers that are not the ones written to the board, so an
            # out-of-range observation reached the database as a real result
            # without ever meeting the ceiling that guards the same column.
            validate_board_score(
                p1_final, p2_final, match_data, data.queen_claimed_by,
                allow_scoreless_queen=True,
            )

            board_patch = {
                "player1_score": p1_final,
                "player2_score": p2_final,
                "status": "completed",
                "board_winner": outcome["board_winner"],
                "p1_coins_pocketed": data.p1_coins_pocketed,
                "p2_coins_pocketed": data.p2_coins_pocketed,
                "coins_remaining_with": data.coins_remaining_with,
                "coins_remaining": data.coins_remaining,
                "queen_pocketed_by": data.queen_pocketed_by or data.queen_claimed_by,
                "queen_covered_by": data.queen_covered_by,
                "finish_type": data.finish_type,
                "special_finish_extra_point": data.special_finish_extra_point,
                "queen_status": outcome["queen_status"],
                "queen_awarded_to": outcome["queen_awarded_to"],
                "base_points": outcome["base_points"],
                "queen_bonus": outcome["queen_bonus"],
                "p1_penalty": data.p1_penalty or 0,
                "p2_penalty": data.p2_penalty or 0,
                "scoring_warnings": outcome["warnings"] or None,
                # Kept in step so the older reads of these two columns agree.
                "queen_claimed_by": data.queen_pocketed_by or data.queen_claimed_by or "none",
                "queen_covered": outcome["queen_status"] == "covered",
                "completed_at": datetime.utcnow().isoformat(),
                # A confirmed board is the official record of a game that has
                # been played. Changing it later is a deliberate act, not a
                # second submission.
                "locked": True,
                "confirmed_by": admin["id"],
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            # Scorers enter the coin count; the queen is added here from the
            # tournament's configured value, and only when it was covered.
            p1_final, p2_final, queen_note = apply_queen_points(
                data.p1_score, data.p2_score,
                data.queen_claimed_by, data.queen_covered, rules,
            )
            board_patch = {
                "player1_score": p1_final,
                "player2_score": p2_final,
                "status": "completed",
                # Infer from raw coin counts, before adding the queen; the
                # queen can belong to the losing side in a custom game.
                "board_winner": (data.board_winner if data.board_winner is not None
                                 else "player1" if data.p1_score > data.p2_score
                                 else "player2" if data.p2_score > data.p1_score
                                 else "none"),
                "queen_claimed_by": data.queen_claimed_by,
                "queen_covered": data.queen_covered,
                "completed_at": datetime.utcnow().isoformat(),
                # Locked, exactly as the remaining-coins branch above does it.
                #
                # update_board's confirmed-board guard is `if pb.get("locked")
                # and not override`, keyed purely on this flag -- and the
                # classic branch never set it. So the protection the scoring
                # screen describes ("re-submitting would quietly rewrite a
                # played game") applied to remaining-coins tournaments only.
                # Probed side by side on identical matches: the remaining-coins
                # board answered 409 to a plain PUT, the classic board accepted
                # it and rewrote 15-4 to 0-25 with no override, no reason, and
                # an audit row recording it as an ordinary correction rather
                # than an override.
                #
                # The degraded-schema strip below pops every
                # _BOARD_DETAIL_COLUMNS key, these three included, so a
                # deployment without migration 005 is unaffected.
                "locked": True,
                "confirmed_by": admin["id"],
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            }

        # Recompute the match from the board set as it will be after this write.
        projected = [
            {**b, **board_patch} if is_target(b) else b
            for b in boards
        ]
        total_sets, _ = set_layout(match_data, rules)
        updated_match = (apply_set_results(match_data, projected, rules)
                         if total_sets > 1 or official_game
                         else recalculate_match_scores(match_data, projected, rules))

        match_patch = {
            "player1_board_wins": updated_match["player1BoardWins"],
            "player2_board_wins": updated_match["player2BoardWins"],
            "player1_total_points": updated_match["player1TotalPoints"],
            "player2_total_points": updated_match["player2TotalPoints"],
            "status": updated_match["status"],
            "winner_id": updated_match["winnerId"],
            "winner_name": updated_match["winnerName"],
            "match_completed_at": updated_match.get("matchCompletedAt"),
        }
        if "tieBreakRequired" in updated_match:
            # An all-boards-played draw needs a human decision, so say so on
            # the match rather than quietly leaving it without a winner.
            #
            # Both scoring models raise this now. It used to be persisted only
            # under remaining-coins, so a classic knockout drawn on board wins
            # -- eight boards at 4-4 -- was stored as completed with no winner
            # and nothing to tell the organiser a decision was owed.
            match_patch["tie_break_required"] = updated_match.get("tieBreakRequired", False)
            match_patch["tie_break_rule"] = updated_match.get("tieBreakRule")
        if total_sets > 1:
            match_patch["player1_sets_won"] = updated_match.get("player1SetsWon", 0)
            match_patch["player2_sets_won"] = updated_match.get("player2SetsWon", 0)


        degraded_note = ""
        if not board_detail_available(admin_db):
            dropped = [k for k in _BOARD_DETAIL_COLUMNS if k in board_patch]
            for k in dropped:
                board_patch.pop(k, None)
            match_patch.pop("tie_break_required", None)
            match_patch.pop("tie_break_rule", None)
            if dropped:
                degraded_note = " [detail not stored: apply migration 005]"

        next_board_number = board_number + 1
        game_finished = (official_game and updated_match["sets"][set_number - 1]["status"] == "completed")
        has_next = (not game_finished and any(b["board_number"] == next_board_number
                       and (b.get("set_number") or 1) == set_number
                       for b in boards))
        opens_next_set = ((not has_next or game_finished) and total_sets > 1
                          and updated_match["status"] != "completed"
                          and (not official_game or game_finished)
                          and any(b["board_number"] == 1
                                  and (b.get("set_number") or 1) == set_number + 1
                                  for b in boards))

        board_row = apply_board_result(
            admin_db,
            match_id=id,
            board_number=board_number,
            board_patch=board_patch,
            match_patch=match_patch,
            audit={
                "admin_id": admin["id"],
                "admin_name": admin["name"],
                "new_score": {"player1": p1_final, "player2": p2_final},
                "reason": (
                    f"{data.audit_reason} (coins {data.p1_score}-{data.p2_score}; {queen_note})"
                    if queen_note else data.audit_reason
                ) + degraded_note,
            },
            next_board_number=next_board_number if has_next else None,
            set_number=set_number if total_sets > 1 else None,
            next_set_number=set_number + 1 if opens_next_set and official_game else None,
        )
        if opens_next_set and not official_game:
            # Existing custom formats keep their old transition path. The
            # official preset uses the atomic RPC above for this hand-off.
            admin_db.table("boards").update({"status": "in_progress"}).eq(
                "match_id", id).eq("set_number", set_number + 1).eq(
                "board_number", 1).eq("status", "pending").execute()

        response = serialize_board(board_row) if board_row else {"status": "ok"}
        guard.store(response)
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{id}/confirm")
async def confirm_match(
    id: str,
    admin = Depends(verify_admin),
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Finalise a match result (spec 71).

    Confirm, advance the winner, notify participants and write the audit record
    all commit together. Repeating the call is a no-op.
    """
    admin_db = get_admin_db()
    guard = IdempotencyGuard(admin_db, idempotency_key, f"POST /matches/{id}/confirm", {"id": id})
    cached = guard.replay()
    if cached is not None:
        return cached

    try:
        m, confirm_tournament = _authorise_match_with_tournament(
            admin_db, id, admin, "match.confirm")
        _assert_tournament_accepts_play(confirm_tournament)
        if not m.get("result_confirmed"):
            _assert_league_standings_mutable(admin_db, m)

        confirm_rules = (confirm_tournament or {}).get("rules") or {}
        if not m.get("winner_id") and confirm_rules.get("setWinnerRule") == "target_points":
            game_boards = admin_db.table("boards").select("*").eq(
                "match_id", id).execute().data or []
            decided = apply_set_results(m, game_boards, confirm_rules)
            if not decided.get("winnerId"):
                raise HTTPException(status_code=409, detail=(
                    "Complete enough games to win the match. A game ends at 25 points "
                    "or after eight boards; a tied game needs a deciding board."))
            settled = {
                "status": "completed", "winner_id": decided["winnerId"],
                "winner_name": decided["winnerName"],
                "player1_board_wins": decided["player1BoardWins"],
                "player2_board_wins": decided["player2BoardWins"],
                "player1_total_points": decided["player1TotalPoints"],
                "player2_total_points": decided["player2TotalPoints"],
                "player1_sets_won": decided["player1SetsWon"],
                "player2_sets_won": decided["player2SetsWon"],
                "match_completed_at": datetime.now(timezone.utc).isoformat(),
            }
            admin_db.table("matches").update(settled).eq("id", id).execute()
            m = {**m, **settled}

        if not m.get("winner_id"):
            # Finishing a match is the organiser's decision, not arithmetic on
            # how many board rows happen to exist. A match can be settled after
            # one board or after eight -- players agree, time runs out, the
            # boards are needed for the next round -- and the result is whatever
            # was actually played.
            #
            # So confirmation recomputes from the boards rather than trusting
            # the stored row, and decides on what it finds. This also repairs a
            # match whose last write did not land: one was sitting at 20-1 with
            # every board complete and still recorded as live with no winner.
            boards = admin_db.table("boards").select("*").eq(
                "match_id", id).order("board_number").execute().data or []
            if not any(board.get("status") == "completed" for board in boards):
                raise HTTPException(
                    status_code=409,
                    detail="Complete at least one board, or record a walkover or award before confirming this match.",
                )
            rules = (confirm_tournament or {}).get("rules") or {}
            recomputed = recalculate_match_scores(m, boards, rules)

            p1_points = recomputed["player1TotalPoints"]
            p2_points = recomputed["player2TotalPoints"]
            p1_wins = recomputed["player1BoardWins"]
            p2_wins = recomputed["player2BoardWins"]

            if scoring_mode(rules) == "remaining_coins":
                lead = p1_points - p2_points
            else:
                lead = p1_wins - p2_wins

            is_league = str(m.get("stage") or "") == "league"

            if lead == 0 and not is_league:
                # A knockout cannot be left level: nothing advances out of it
                # and the bracket stops there. That needs a human either way.
                rule = m.get("tie_break_rule") or (rules.get("tieBreak") or "organizer_decision")
                how = ("Play a deciding board, or award the match."
                       if rule == "additional_board"
                       else "Award the match to one of the players.")
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This match is level at {} points each ({} board(s) played), "
                        "so there is no winner to confirm. {}"
                    ).format(p1_points, len([b for b in boards if b.get("status") == "completed"]), how),
                )

            # A level LEAGUE match is a draw, and a draw is a result.
            #
            # This refused every stage alike, so a drawn league match could
            # never be confirmed at all -- and everything downstream of that
            # was unreachable with it. `calculate_points_table` has a whole
            # branch awarding pointsForDraw and a D column to show it;
            # recalculate_match_scores deliberately leaves a level league
            # match drawn and says so in its own comment. None of it could
            # ever run. Worse, the match stayed unconfirmed for good, so
            # league_is_complete never became true and a league+knockout
            # tournament with one drawn match could never promote anybody.
            #
            # Probed: two players level after 1-1 boards, confirm answered 409
            # and result_confirmed stayed false with no way forward but to
            # award the match to somebody who did not win it.
            drawn = lead == 0
            winner_is_p1 = lead > 0
            settled = {
                "status": "completed",
                "winner_id": None if drawn else (
                    m.get("player1_id") if winner_is_p1 else m.get("player2_id")),
                "winner_name": None if drawn else (
                    m.get("player1_name") if winner_is_p1 else m.get("player2_name")),
                "player1_board_wins": p1_wins,
                "player2_board_wins": p2_wins,
                "player1_total_points": p1_points,
                "player2_total_points": p2_points,
                "match_completed_at": datetime.now(timezone.utc).isoformat(),
            }
            if board_detail_available(admin_db):
                settled["tie_break_required"] = False
            admin_db.table("matches").update(settled).eq("id", id).execute()
            m = {**m, **settled}

        winner_name = m.get("winner_name")
        recipients = resolve_tournament_audience(admin_db, m["tournament_id"])

        notifications = [
            {
                "profile_id": profile_id,
                "tournament_id": m["tournament_id"],
                "title": "Match Result Confirmed",
                "message": (
                    f"Match #{m['match_number']} ({m['player1_name']} vs {m['player2_name']}) "
                    f"has been officially finalized. Winner: {winner_name or 'Draw'}."
                ),
                "type": "result_confirmed",
            }
            for profile_id in recipients
        ]

        if m.get("next_match_id") and m.get("winner_id"):
            notifications.extend([
                {
                    "profile_id": profile_id,
                    "tournament_id": m["tournament_id"],
                    "title": "Bracket Advanced!",
                    "message": (
                        f"Congratulations to '{winner_name}' for winning match "
                        f"#{m['match_number']} and advancing to the next knockout round!"
                    ),
                    "type": "knockout_advanced",
                }
                for profile_id in recipients
            ])

        result = confirm_match_result(
            admin_db,
            match_id=id,
            actor_id=admin["id"],
            actor_name=admin["name"],
            notifications=notifications,
        )

        # Finishing the league fills the knockout bracket from the standings.
        promotion = None
        if not result.get("already_confirmed") and m.get("stage") == "league":
            promotion = try_auto_promote(admin_db, m["tournament_id"])

        response = {
            "status": "success",
            "message": (
                "Match result was already confirmed."
                if result.get("already_confirmed")
                else "Match results confirmed."
            ),
            **result,
        }
        if promotion and promotion.get("promotedCount"):
            response["qualifiersPromoted"] = promotion["promotedCount"]
            response["message"] += (
                f" League complete - {promotion['promotedCount']} knockout slot(s) filled."
            )
        guard.store(response)
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{id}/reopen")
async def reopen_match(id: str, data: MatchReopenSchema, admin = Depends(verify_admin)):
    """
    Take a confirmed result back so its boards can be corrected.

    Confirming is final on purpose: it advances the winner, tells everyone and
    closes the match to scoring. But umpires transpose scores, and a result
    found wrong after confirmation had no way back -- every scoring route
    refused a confirmed match and pointed at a correction workflow that did
    not exist. This is that workflow.

    It is an owner's action, not a scorer's. A scorer records what happened at
    the board; undoing an official result, and pulling a player back out of
    the next round, is the organiser's call.

    The state machine lists a match's 'completed' as terminal and says the only
    way out is a correction workflow. Being that workflow, this changes the
    status directly rather than through validate_match_transition, which would
    -- correctly, for every other caller -- refuse the move.
    """
    admin_db = get_admin_db()
    try:
        match = _authorise_match(admin_db, id, admin, "match.reopen")
        reason = data.reason

        if not match.get("result_confirmed"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This result has not been confirmed, so there is nothing to "
                    "reopen. Correct the board directly."
                ),
            )

        _assert_league_standings_mutable(admin_db, match)

        # The winner may already be playing the next round. Taking their result
        # back while that match is under way would leave a player in a match
        # they may no longer have qualified for, with boards already played
        # against them -- so the later match has to be untouched, or itself
        # reopened first.
        next_id = match.get("next_match_id")
        next_match = None
        if next_id:
            rows = admin_db.table("matches").select("*").eq("id", next_id).execute().data
            next_match = rows[0] if rows else None
        if next_match is not None:
            next_boards = admin_db.table("boards").select("*").eq(
                "match_id", next_id).execute().data or []
            under_way = (
                next_match.get("result_confirmed")
                or next_match.get("status") in ("live", "paused")
                or any(b.get("status") == "completed" for b in next_boards)
            )
            if under_way:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The winner of this match has gone on to match #{} ({} vs {}), "
                        "which is under way or already played. Reopen that match "
                        "first, or let this result stand."
                    ).format(
                        next_match.get("match_number"),
                        next_match.get("player1_name") or "TBD",
                        next_match.get("player2_name") or "TBD",
                    ),
                )

        reopened = {
            "result_confirmed": False,
            "result_confirmed_at": None,
            "status": "live",
            "match_completed_at": None,
            # The winner goes too. Confirming recomputes it from the boards, so
            # keeping it here bought nothing and cost the one thing the
            # organiser reopened the match to be rid of: a match sitting live
            # and unconfirmed while still announcing the wrong player as the
            # winner, on the fixture card and on the public board. The slot
            # clearing below reads `match`, which was fetched before this
            # write, so it still knows who to pull out of the next round.
            "winner_id": None,
            "winner_name": None,
        }
        res = admin_db.table("matches").update(reopened).eq("id", id).execute()

        # The boards are no longer confirmed either.
        #
        # This route exists to "take a confirmed result back so its boards can
        # be corrected", and a locked board refuses correction without an
        # override and a reason. Reopening therefore has to release the lock,
        # or it does not do the one thing it is for.
        #
        # It never used to, and the workflow only worked by accident: the
        # classic scoring path was not setting `locked` at all, so classic
        # boards were correctable because they were never protected. Locking
        # them -- which is what stops a played board being silently rewritten
        # -- turned reopen into a dead end, and twelve reopen invariants said
        # so immediately. Releasing the lock here is the half that was missing.
        #
        # Guarded on board_detail_available for the same reason every other
        # write of these columns is: they arrive with migration 005.
        if board_detail_available(admin_db):
            try:
                admin_db.table("boards").update({
                    "locked": False,
                    "confirmed_by": None,
                    "confirmed_at": None,
                }).eq("match_id", id).execute()
            except Exception as e:
                logger.warning(
                    "Reopened match %s but could not release its board locks: %s", id, e)

        # If the tournament was already finished, it is not any more.
        #
        # Reopening the match that decided a completed tournament used to
        # succeed while leaving the tournament 'completed' with its recorded
        # champion intact -- so the public page showed a champion beside a
        # final that now had no winner, /complete refused to re-run ("already
        # completed"), and there was no way to put either right.
        #
        # The correction is the point of this route, so the tournament comes
        # back to in_progress with the champion cleared, and is completed
        # again once the corrected result is confirmed.
        reopened_tournament = (admin_db.table("tournaments").select(
            "id, status, champion_name").eq(
            "id", match["tournament_id"]).execute().data or [None])[0]
        reopened_event = False
        if reopened_tournament and canonical_tournament_status(
                reopened_tournament.get("status")) == "completed":
            try:
                admin_db.table("tournaments").update({
                    "champion_id": None,
                    "champion_name": None,
                    "completed_at": None,
                }).eq("id", match["tournament_id"]).execute()
                set_tournament_status(admin_db, match["tournament_id"], "in_progress")
                reopened_event = True
                logger.info(
                    "Tournament %s was completed; reopening match %s returned it to "
                    "in_progress and cleared champion %r.",
                    match["tournament_id"], id, reopened_tournament.get("champion_name"),
                )
            except Exception as e:
                # The match is already reopened; say the tournament did not
                # follow rather than fail the correction the organiser needs.
                logger.error(
                    "Reopened match %s but could not reopen tournament %s: %s",
                    id, match["tournament_id"], e,
                )

        # Pull the winner back out of the slot they were advanced into -- and
        # only them. If someone else is standing there the bracket has been
        # edited by hand since, and guessing would be worse than leaving it.
        slot_cleared = None
        if next_match is not None:
            slot = match.get("next_match_slot") or "player2"
            occupant = next_match.get(f"{slot}_id")
            if occupant is not None and str(occupant) == str(match.get("winner_id")):
                admin_db.table("matches").update({
                    f"{slot}_id": None,
                    f"{slot}_name": None,
                }).eq("id", next_id).execute()
                slot_cleared = slot

        record_audit(
            admin_db, actor=admin, action="match.reopen",
            entity_type="match", entity_id=id,
            previous_state={
                "status": match.get("status"),
                "result_confirmed": True,
                "result_confirmed_at": match.get("result_confirmed_at"),
                "winner_id": match.get("winner_id"),
            },
            new_state={
                "status": "live",
                "result_confirmed": False,
                "next_match_slot_cleared": slot_cleared,
            },
            request_context={"reason": reason, "tournament_id": match.get("tournament_id")},
        )

        # The score history is what an organiser reads when a result is
        # questioned, so the reopening belongs in it, next to the corrections
        # that follow. It is a match-level entry: 0 is not a board number.
        totals = {
            "player1": match.get("player1_total_points") or 0,
            "player2": match.get("player2_total_points") or 0,
        }
        try:
            admin_db.table("score_audit_logs").insert({
                "match_id": id,
                "admin_id": admin["id"],
                "admin_name": admin["name"],
                "board_number": 0,
                "previous_score": totals,
                "new_score": totals,
                "reason": "Result reopened for correction: " + reason,
            }).execute()
        except Exception as e:
            # The result is already reopened. Reporting a missing history line
            # as a failure would invite a retry, and the retry would 409.
            logger.error(f"Score audit write failed for match.reopen on {id}: {str(e)}")

        fan_out_notification(
            admin_db,
            title="Match Result Reopened",
            message=(
                f"The result of match #{match.get('match_number')} "
                f"({match.get('player1_name')} vs {match.get('player2_name')}) has been "
                f"reopened for correction: {reason}"
            ),
            # notifications.type is a CHECK over a fixed list with no entry for
            # a reopened result. This is the type the result's audience already
            # receives, and a row with an unlisted type would be rejected and
            # silently delivered to nobody.
            type="result_confirmed",
            tournament_id=match.get("tournament_id"),
        )

        return serialize_match(res.data[0] if res.data else {**match, **reopened})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{id}/tie-break")
async def resolve_tie_break(id: str, data: TieBreakSchema, admin = Depends(verify_admin)):
    """
    Record the organiser's ruling on a match that finished level.

    Under remaining-coins scoring a match can end with the points exactly
    level. The engine sets tie_break_required and returns no winner, which is
    correct -- an extra board, sudden death or an organiser's ruling all need a
    human. But nothing could then supply that human decision: /confirm refuses
    a match with no winner, the scoring screen offers no way to name one, and
    the league can never reach a full set of confirmed results.

    The ruling is recorded in tie_break_result alongside the winner, so the
    standings show a decided match and the reason it was decided that way
    survives with it.
    """
    admin_db = get_admin_db()
    try:
        match, tb_tournament = _authorise_match_with_tournament(
            admin_db, id, admin, "match.confirm")
        _assert_tournament_accepts_play(tb_tournament)
        _assert_league_standings_mutable(admin_db, match)

        if match.get("result_confirmed"):
            raise HTTPException(
                status_code=409,
                detail="This result is already confirmed.",
            )

        # Only a match that is ACTUALLY level may be ruled on.
        #
        # The route checked that the named winner was one of the two players
        # and that a reason was given, but never that there was a tie to
        # break. So it would take a cleanly decided match and hand it to the
        # other player: probed a 2-0 win, called tie-break naming the loser,
        # got 200 and a match whose winner had lost it. The board wins and
        # points were left untouched, so the record then contradicted itself.
        #
        # Level is recomputed from the boards, the same way /confirm does it,
        # rather than trusted from the row -- and tie_break_required is
        # honoured on its own because the sets layer sets it for a match that
        # is level on sets rather than on this comparison.
        tb_boards = admin_db.table("boards").select("*").eq(
            "match_id", id).order("board_number").execute().data or []
        tb_rules = (tb_tournament or {}).get("rules") or {}
        if tb_rules.get("setWinnerRule") == "target_points":
            raise HTTPException(status_code=409,
                                detail="Play the deciding board for a tied game.")
        tb_recomputed = recalculate_match_scores(match, tb_boards, tb_rules)
        if scoring_mode(tb_rules) == "remaining_coins":
            tb_lead = (tb_recomputed["player1TotalPoints"]
                       - tb_recomputed["player2TotalPoints"])
        else:
            tb_lead = (tb_recomputed["player1BoardWins"]
                       - tb_recomputed["player2BoardWins"])

        if tb_lead != 0 and not match.get("tie_break_required"):
            ahead = (match.get("player1_name") if tb_lead > 0
                     else match.get("player2_name"))
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This match is not level -- {ahead} is ahead, so there is nothing "
                    "to rule on. Confirm the result, or reopen it if the boards are wrong."
                ),
            )

        p1, p2 = match.get("player1_id"), match.get("player2_id")
        if data.winner_id not in (p1, p2):
            raise HTTPException(
                status_code=422,
                detail="The winner must be one of the two players in this match.",
            )
        if not (data.reason or "").strip():
            raise HTTPException(
                status_code=422,
                detail="A reason is required: a level match decided without one cannot be explained later.",
            )

        winner_is_p1 = data.winner_id == p1
        patch = {
            "status": "completed",
            "winner_id": data.winner_id,
            "winner_name": match.get("player1_name") if winner_is_p1 else match.get("player2_name"),
            "match_completed_at": datetime.now(timezone.utc).isoformat(),
            "tie_break_required": False,
            "tie_break_result": data.reason.strip(),
        }
        # Migration 005 carries the tie-break columns; without it the ruling
        # still resolves the match, it just cannot record why.
        if not board_detail_available(admin_db):
            patch.pop("tie_break_required", None)
            patch.pop("tie_break_result", None)

        res = admin_db.table("matches").update(patch).eq("id", id).execute()
        record_audit(
            admin_db, actor=admin, action="match.tie_break",
            entity_type="match", entity_id=id,
            previous_state={"winner_id": match.get("winner_id"),
                            "p1": match.get("player1_total_points"),
                            "p2": match.get("player2_total_points")},
            new_state={"winner_id": data.winner_id, "reason": data.reason.strip()},
        )
        return serialize_match(res.data[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{id}/walkover")
async def record_walkover(id: str, data: WalkoverSchema, admin = Depends(verify_admin)):
    """
    Award a match nobody played: a no-show, a retirement, or a concession.

    Before this existed the only way to finish a match was to score boards, so
    an organiser facing an absent player had to invent scores — which then went
    into the points table indistinguishable from a real result.

    The match is given a winner, the board wins needed to take it, and the
    points the rules say a walkover is worth, so the standings need no special
    case. What it is NOT given is coin points by default: nobody pocketed
    anything, and inflating score difference with a match that was never played
    would distort the very tie-break it feeds.
    """
    admin_db = get_admin_db()
    try:
        match, walkover_tournament = _authorise_match_with_tournament(
            admin_db, id, admin, "match.walkover")
        _assert_tournament_accepts_play(walkover_tournament)
        _assert_league_standings_mutable(admin_db, match)

        if match.get("result_confirmed"):
            raise HTTPException(
                status_code=409,
                detail="This result is already confirmed. Reopen it before recording a walkover.",
            )

        p1, p2 = match.get("player1_id"), match.get("player2_id")
        if data.winner_id not in (p1, p2):
            raise HTTPException(
                status_code=422,
                detail="The winner must be one of the two players in this match.",
            )
        if not (data.reason or "").strip():
            raise HTTPException(status_code=422, detail="A reason is required for a walkover.")

        rules = (walkover_tournament or {}).get("rules") or {}
        max_boards = match.get("max_boards") or rules.get("maxBoardsPerMatch") or 8
        # Enough boards to have taken the match, not all of them: a 3-board
        # match is won 2-0, and recording 3-0 would overstate it.
        default_wins = (int(max_boards) // 2) + 1
        board_wins = int(rules.get("walkoverBoardWins", default_wins))
        points = int(rules.get("walkoverPoints", 0))

        winner_is_p1 = data.winner_id == p1
        patch = {
            "status": "completed",
            "winner_id": data.winner_id,
            "winner_name": match.get("player1_name") if winner_is_p1 else match.get("player2_name"),
            "player1_board_wins": board_wins if winner_is_p1 else 0,
            "player2_board_wins": 0 if winner_is_p1 else board_wins,
            "player1_total_points": points if winner_is_p1 else 0,
            "player2_total_points": 0 if winner_is_p1 else points,
            "match_completed_at": datetime.now(timezone.utc).isoformat(),
            "walkover": True,
            "walkover_reason": data.reason.strip(),
            "walkover_by": admin["id"],
        }

        # Until migration 010 is applied there is nowhere to record that this
        # was a walkover. The RESULT is still correct and the tournament can go
        # on, so the flag is dropped rather than the organiser being blocked
        # mid-event -- but the response says so, because a walkover that looks
        # like a played win is exactly the confusion this endpoint exists to end.
        present = walkover_columns(admin_db)
        missing = [c for c in _WALKOVER_COLUMNS if c not in present]
        for key in missing:
            patch.pop(key, None)
        # Only the flag itself matters for telling a walkover from a played
        # win; losing walkover_by costs accountability, not correctness.
        degraded = "walkover" in missing

        res = admin_db.table("matches").update(patch).eq("id", id).execute()

        record_audit(
            admin_db, actor=admin, action="match.walkover",
            entity_type="match", entity_id=id,
            previous_state={"status": match.get("status"), "winner_id": match.get("winner_id")},
            new_state={"winner_id": data.winner_id, "reason": data.reason.strip()},
        )

        out = serialize_match(res.data[0])
        if degraded:
            out["warning"] = (
                "Recorded, but this database cannot yet mark it as a walkover. "
                "Apply migration 010 so the result is not mistaken for a played win."
            )

        # A walkover can be the last league result, and then it is what
        # finishes the league -- so it seeds the bracket, exactly as confirming
        # a played match does at :1410. Without this the organiser is left to
        # notice for themselves that the table is final and press Promote by
        # hand; every other way of ending a league does it for them.
        if match.get("stage") == "league":
            promotion = try_auto_promote(admin_db, match["tournament_id"])
            if promotion and promotion.get("promotedCount"):
                out["qualifiersPromoted"] = promotion["promotedCount"]

        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{id}/toss")
async def record_toss(id: str, data: TossSchema, admin = Depends(verify_admin)):
    """
    Record the toss for a match.

    Runs before the first board. The winning side is stored along with what
    they chose, so the match card, the printed sheet and the audit trail all
    show how the match began rather than it living only in the umpire's head.
    """
    admin_db = get_admin_db()
    try:
        match = _authorise_match(admin_db, id, admin, "match.start")

        if match.get("result_confirmed"):
            raise HTTPException(
                status_code=409,
                detail="This match is already finished; the toss cannot be changed.",
            )

        if data.choice not in ("strike", "side"):
            raise HTTPException(status_code=422, detail="Choice must be 'strike' or 'side'.")
        if data.coin_result not in (None, "black", "white"):
            raise HTTPException(status_code=422, detail="Coin result must be 'black' or 'white'.")

        # The winner must be one of the two sides actually in this match.
        sides = {
            match.get("player1_id"): match.get("player1_name"),
            match.get("player2_id"): match.get("player2_name"),
        }
        winner_id = data.toss_winner_id
        if winner_id and winner_id not in sides:
            raise HTTPException(
                status_code=422,
                detail="The toss winner must be one of the two sides in this match.",
            )
        winner_name = data.toss_winner_name or sides.get(winner_id)

        patch = {
            "toss_coin_result": data.coin_result,
            "toss_winner_id": winner_id,
            "toss_winner_name": winner_name,
            "toss_choice": data.choice,
            "toss_recorded_at": datetime.now(timezone.utc).isoformat(),
            "toss_recorded_by": admin["id"],
        }

        try:
            res = admin_db.table("matches").update(patch).eq("id", id).execute()
        except Exception as e:
            if "toss_" not in str(e):
                raise
            raise HTTPException(
                status_code=503,
                detail=(
                    "The toss cannot be saved on this database yet. "
                    "Apply backend/db/migrations/004_match_toss.sql."
                ),
            )

        record_audit(
            admin_db, actor=admin, action="match.toss",
            entity_type="match", entity_id=id,
            new_state={"winner": winner_name, "choice": data.choice,
                       "coin": data.coin_result},
            request_context={"tournament_id": match.get("tournament_id")},
        )
        return serialize_match(res.data[0]) if res.data else {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{id}/sides")
async def set_match_sides(id: str, data: MatchSidesSchema, admin = Depends(verify_admin)):
    """
    Record the coin each side plays, and which way round they are on screen.

    The colour is stored against the player id, and swapping the screen sets a
    presentation flag only. That separation is the point: an umpire standing on
    the other side of the board flips the display, and the queen recorded as
    covered by player 2 must still mean the same person afterwards.
    """
    admin_db = get_admin_db()
    try:
        match = _authorise_match(admin_db, id, admin, "match.start")

        if match.get("result_confirmed"):
            raise HTTPException(
                status_code=409,
                detail="This match is finished; the sides cannot be changed.",
            )

        colors = (data.player1_color, data.player2_color)
        for c in colors:
            if c not in (None, "black", "white"):
                raise HTTPException(status_code=422, detail="Colour must be 'black' or 'white'.")
        if colors[0] and colors[1] and colors[0] == colors[1]:
            raise HTTPException(
                status_code=422,
                detail="Both players cannot play the same colour.",
            )

        patch = {}
        if data.player1_color is not None:
            patch["player1_color"] = data.player1_color
        if data.player2_color is not None:
            patch["player2_color"] = data.player2_color
        if data.sides_swapped is not None:
            patch["sides_swapped"] = data.sides_swapped
        if data.table_number is not None:
            patch["table_number"] = data.table_number
        if data.referee_id is not None:
            patch["referee_id"] = data.referee_id
            ref = admin_db.table("profiles").select("name").eq("id", data.referee_id).execute().data
            patch["referee_name"] = ref[0]["name"] if ref else None

        if not patch:
            return serialize_match(match)

        try:
            res = admin_db.table("matches").update(patch).eq("id", id).execute()
        except Exception as e:
            if "player1_color" not in str(e) and "sides_swapped" not in str(e) \
               and "table_number" not in str(e) and "referee_id" not in str(e):
                raise
            raise HTTPException(
                status_code=503,
                detail=(
                    "Sides cannot be saved on this database yet. "
                    "Apply backend/db/migrations/006_sets_and_sides.sql."
                ),
            )

        record_audit(
            admin_db, actor=admin, action="match.sides",
            entity_type="match", entity_id=id, new_state=patch,
            request_context={"tournament_id": match.get("tournament_id")},
        )
        return serialize_match(res.data[0]) if res.data else {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{id}/sets")
async def get_match_sets(id: str):
    """Per-set totals for a match: points each way, and who took each set."""
    admin_db = get_admin_db()
    try:
        rows = admin_db.table("matches").select("*").eq("id", id).execute().data
        if not rows:
            raise HTTPException(status_code=404, detail="Match not found.")
        match = rows[0]
        boards = admin_db.table("boards").select("*").eq("match_id", id).execute().data or []
        rules = tournament_rules(admin_db, match["tournament_id"]) or {}
        return {
            "matchId": id,
            "numberOfSets": set_layout(match, rules)[0],
            "sets": summarise_sets(match, boards, rules),
            "player1SetsWon": match.get("player1_sets_won", 0),
            "player2SetsWon": match.get("player2_sets_won", 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{id}")
async def remove_match(
    id: str,
    force: bool = Query(False,
                        description="Delete a fixture that has play recorded on it."),
    admin = Depends(verify_admin),
):
    """
    Remove one fixture from a draw.

    The counterpart to POST /tournaments/{id}/matches. A draw acquires fixtures
    that should not be played -- a withdrawal, a pair fixtured twice, a
    play-off added by mistake -- and until now the only way to be rid of one
    was to regenerate the whole draw, which deletes every result in the
    tournament.

    The database deletes the match, its boards, propagated bracket slot and
    audit row in one transaction. An unplayed fixture deletes freely; one
    with play requires force. A knockout match with feeder matches cannot be
    removed until those feeders are removed first.
    """
    admin_db = get_admin_db()
    try:
        match, _tournament = _authorise_match_with_tournament(
            admin_db, id, admin, "tournament.manage")
        assert_tournament_not_terminal(_tournament, "edit its fixtures")
        _assert_league_standings_mutable(admin_db, match)
        tournament_id = match["tournament_id"]

        try:
            result = admin_db.rpc("delete_match_safely", {
                "p_match_id": id, "p_force": force, "p_actor_id": admin["id"],
            }).execute().data or {}
        except Exception as exc:
            detail = str(exc)
            if "PGRST202" in detail or "could not find the function" in detail.lower():
                raise HTTPException(status_code=503, detail="Match deletion needs database migration 021.") from exc
            if any(term in detail.lower() for term in ("play", "feeder", "advance", "terminal", "cannot delete")):
                raise HTTPException(status_code=409, detail=detail)
            raise

        old = result.get("match") or match
        return {
            "status": "success",
            "message": "Match {} ({} v {}) was removed.".format(
                old.get("match_number"), old.get("player1_name"), old.get("player2_name")),
            "matchId": id,
            "tournamentId": tournament_id,
            "stage": old.get("stage"),
            "boardsDeleted": result.get("boardsDeleted", 0),
            "discardedPlay": result.get("discardedPlay", False),
            "orphanedFeeders": [],
            "clearedSlot": result.get("clearedSlot"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Deleting match %s failed: %s", id, e)
        raise HTTPException(status_code=400, detail=str(e))


def _approved_entrants(admin_db, tournament_id: str) -> Dict[str, Any]:
    """{participant_id: {name, type}} for everyone approved in a tournament."""
    regs = admin_db.table("registrations").select(
        "*, player:profiles(*), team:teams(*)"
    ).eq("tournament_id", tournament_id).eq("status", "approved").execute().data or []

    entrants: Dict[str, Any] = {}
    for r in regs:
        if r.get("type") == "singles" and r.get("player"):
            entrants[r["player"]["id"]] = {"name": r["player"]["name"], "type": "singles"}
        elif r.get("type") == "doubles" and r.get("team"):
            entrants[r["team"]["id"]] = {"name": r["team"]["name"], "type": "doubles"}
    return entrants


def _schedule_clashes(admin_db, match: Dict[str, Any], tournament_id: str,
                      date: Optional[str], time: Optional[str],
                      board: Optional[int], sides: tuple) -> List[str]:
    """
    Who else is already booked into this slot.

    Reported, not refused. An organiser moving a match knows things the draw
    does not -- a board freed early, a pair who agreed to play late -- and the
    auto-scheduler is there for anyone who wants the conflict-free version.
    What they must not do is create a clash without being told.
    """
    if not date or not time:
        return []

    others = admin_db.table("matches").select(
        "id, match_number, board_number, scheduled_date, scheduled_time, "
        "player1_id, player2_id, player1_name, player2_name"
    ).eq("tournament_id", tournament_id).eq(
        "scheduled_date", date).eq("scheduled_time", time).execute().data or []

    clashes = []
    for other in others:
        if other["id"] == match["id"]:
            continue
        if board is not None and other.get("board_number") == board:
            clashes.append(
                "board %s at %s on %s is already match %s"
                % (board, time, date, other.get("match_number")))
        for pid in sides:
            if pid and pid in (other.get("player1_id"), other.get("player2_id")):
                name = (other.get("player1_name") if pid == other.get("player1_id")
                        else other.get("player2_name"))
                clashes.append(
                    "%s is already playing match %s at that time"
                    % (name, other.get("match_number")))
    return clashes


@router.put("/{id}")
async def update_match_fixture(
    id: str,
    data: MatchFixtureUpdateSchema,
    force: bool = Query(False,
                        description="Re-pair a fixture that has play recorded on it."),
    admin = Depends(verify_admin),
):
    """
    Edit a fixture: who plays it, what round it belongs to, and when and where.

    The U of the draw's CRUD. A fixture could be created and removed but never
    corrected, so a pairing entered wrong, a match moved to another board, or a
    round misnamed all had to be deleted and made again -- which loses the
    match number, and any boards already scored with it.

    This does not touch the result. Scores, board wins and the winner are the
    scoring engine's, and are changed by correcting boards and reconfirming.
    Only the fields present in the request are written.
    """
    admin_db = get_admin_db()
    try:
        match, _tournament = _authorise_match_with_tournament(
            admin_db, id, admin, "tournament.manage")
        tournament_id = match["tournament_id"]

        patch: Dict[str, Any] = {}
        warnings: List[str] = []

        # ---- who plays -----------------------------------------------------
        repairing = (
            (data.player1_id is not None and data.player1_id != match.get("player1_id"))
            or (data.player2_id is not None and data.player2_id != match.get("player2_id"))
        )
        moving_stage = data.stage is not None and data.stage != match.get("stage")

        if repairing or moving_stage:
            _assert_league_standings_mutable(admin_db, match)
            if data.stage == "league":
                _assert_league_standings_mutable(admin_db, {**match, "stage": "league"})
            boards = admin_db.table("boards").select(
                "id, status, player1_score, player2_score"
            ).eq("match_id", id).execute().data or []
            # A board is play when it is finished or carries a score. Every
            # match is drawn with its first board already in_progress, which is
            # how it is queued for the umpire, not a sign it was played.
            played = [
                b for b in boards
                if b.get("status") == "completed"
                or (b.get("player1_score") or 0)
                or (b.get("player2_score") or 0)
            ]
            if (match.get("result_confirmed") or played) and not force:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Match {} has {} and {} board(s) scored. Changing who plays it, or "
                        "which stage it belongs to, would attach those scores to a different "
                        "pairing and rewrite the points table under them. Reopen and correct "
                        "the boards instead, or confirm you want the fixture re-paired."
                    ).format(
                        match.get("match_number"),
                        "a confirmed result" if match.get("result_confirmed") else "no confirmed result",
                        len(played),
                    ),
                )
            if match.get("result_confirmed") or played:
                warnings.append(
                    "%d board(s) and any result stay on this fixture under the new pairing"
                    % len(played))

        if repairing:
            p1 = data.player1_id if data.player1_id is not None else match.get("player1_id")
            p2 = data.player2_id if data.player2_id is not None else match.get("player2_id")
            if p1 and p2 and p1 == p2:
                raise HTTPException(
                    status_code=422,
                    detail="A player cannot be fixtured against themselves.")

            entrants = _approved_entrants(admin_db, tournament_id)
            for slot, pid in (("Player 1", data.player1_id), ("Player 2", data.player2_id)):
                if pid is None:
                    continue
                if pid not in entrants:
                    raise HTTPException(
                        status_code=422,
                        detail="%s is not an approved entrant in this tournament." % slot)

            # Singles and doubles are separate competitions; a knockout slot
            # that is still a placeholder has no type of its own to compare.
            types = {entrants[pid]["type"]
                     for pid in (p1, p2) if pid and pid in entrants}
            if len(types) > 1:
                raise HTTPException(
                    status_code=422,
                    detail="A singles player cannot be fixtured against a doubles team.")

            for n, pid in ((1, data.player1_id), (2, data.player2_id)):
                if pid is None:
                    continue
                patch["player%d_id" % n] = pid
                patch["player%d_name" % n] = entrants[pid]["name"]

        if moving_stage:
            patch["stage"] = data.stage
            if match.get("result_confirmed"):
                warnings.append(
                    "a confirmed result moved out of the league leaves the points table; "
                    "moved into it, it joins")

        if data.round_name is not None:
            patch["round_name"] = data.round_name

        # ---- when and where -------------------------------------------------
        if data.board_number is not None:
            patch["board_number"] = data.board_number
        if data.scheduled_date is not None:
            patch["scheduled_date"] = data.scheduled_date
        if data.scheduled_time is not None:
            patch["scheduled_time"] = data.scheduled_time

        if not patch:
            raise HTTPException(
                status_code=422,
                detail="Nothing to change. Send at least one field to update.")

        # Use the same duration, rest and participant-level check as schedule
        # publication. An exact-start comparison missed overlapping matches
        # (10:00 and 10:15 on the same board) and two different doubles teams
        # containing the same player.
        all_matches = admin_db.table("matches").select("*").eq(
            "tournament_id", tournament_id).execute().data or []
        proposed_matches = [{**row, **patch} if row.get("id") == id else row
                            for row in all_matches]
        team_ids = {side for row in proposed_matches if row.get("type") == "doubles"
                    for side in (row.get("player1_id"), row.get("player2_id")) if side}
        team_members = {}
        if team_ids:
            teams = admin_db.table("teams").select("id, player1_id, player2_id").in_(
                "id", list(team_ids)).execute().data or []
            team_members = {team["id"]: [pid for pid in (
                team.get("player1_id"), team.get("player2_id")) if pid]
                            for team in teams}
        rules = (_tournament or {}).get("rules") or {}
        def conflicts(rows):
            return detect_schedule_conflicts(
                rows, team_members,
                int(rules.get("matchDurationMinutes") or 30),
                int(rules.get("restTimeMinutes") if rules.get("restTimeMinutes") is not None else 10),
                tournament_start_date=_tournament.get("tournament_start_date"),
                tournament_end_date=_tournament.get("tournament_end_date"),
                number_of_boards=_tournament.get("number_of_boards"),
            )
        def signature(conflict):
            return (conflict["type"], tuple(sorted(str(n) for n in conflict["matchNumbers"])))
        before = {signature(c) for c in conflicts(all_matches)}
        changed_number = match.get("match_number")
        introduced = [c for c in conflicts(proposed_matches)
                      if changed_number in c.get("matchNumbers", [])
                      and signature(c) not in before]
        if introduced and canonical_tournament_status(
                _tournament.get("status")) in ("fixture_published", "in_progress"):
            raise HTTPException(status_code=409, detail=(
                "This fixture change would conflict with the published schedule: "
                + "; ".join(c["detail"] for c in introduced)))
        warnings.extend(c["detail"] for c in introduced)

        admin_db.table("matches").update(patch).eq("id", id).execute()

        record_audit(
            admin_db, actor=admin, action="match.update_fixture",
            entity_type="match", entity_id=id,
            previous_state={k: match.get(k) for k in patch},
            new_state=dict(patch),
            request_context={
                "forced": force,
                "reason": data.reason,
                "repaired": repairing,
                "warnings": warnings,
            },
        )

        updated = (admin_db.table("matches").select("*").eq(
            "id", id).execute().data or [match])[0]
        boards = admin_db.table("boards").select("*").eq(
            "match_id", id).order("board_number").execute().data or []

        return {
            "status": "success",
            "message": "Match %s was updated." % updated.get("match_number"),
            "changed": sorted(patch),
            # Said out loud rather than enforced: the organiser may have a
            # reason, but must not double-book a board by accident.
            "warnings": warnings,
            "match": serialize_match(updated, boards=boards),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Updating match %s failed: %s", id, e)
        raise HTTPException(status_code=400, detail=str(e))

def _missing_set_tie_breaks(error: Exception) -> bool:
    """Whether this failure is the 029 column not being there yet."""
    text = str(error).lower()
    return "set_tie_breaks" in text and any(
        m in text for m in ("does not exist", "42703", "pgrst204", "schema cache"))


@router.post("/{id}/sets/{set_number}/tie-break")
async def resolve_set_tie_break(
    id: str,
    set_number: int,
    data: TieBreakSchema,
    admin = Depends(verify_admin),
):
    """
    Record the umpire's sudden-death ruling on ONE game of a match.

    The AICF 21-point / six-board age-group variant settles a level sixth
    board by sudden death rather than by playing a seventh. That distinction
    is not cosmetic: a seventh board would manufacture coins and move net
    score difference, which is the league's tie-break, so the ruling is stored
    against the game instead of being scored as play.

    Distinct from POST /{id}/tie-break, which rules on a whole MATCH that
    finished level. This rules on one game inside a match that is still
    running, and the match carries on: the next game's first board opens.

    `matches.set_tie_breaks` is the store, keyed by game number as a string,
    and `summarise_sets` reads exactly this shape -- method 'sudden_death'
    plus a winnerId that is one of the two players -- to mark the game
    complete (scoring_engine, "validated_decision").
    """
    admin_db = get_admin_db()
    try:
        match, tb_tournament = _authorise_match_with_tournament(
            admin_db, id, admin, "match.confirm")
        _assert_tournament_accepts_play(tb_tournament)

        if match.get("result_confirmed"):
            raise HTTPException(
                status_code=409,
                detail="This result is already confirmed. Reopen it before ruling on a game.",
            )

        p1, p2 = match.get("player1_id"), match.get("player2_id")
        if data.winner_id not in (p1, p2):
            raise HTTPException(
                status_code=422,
                detail="The winner must be one of the two players in this match.",
            )
        if not (data.reason or "").strip():
            raise HTTPException(
                status_code=422,
                detail=("A reason is required: a game decided without one cannot be "
                        "explained later."),
            )

        rules = (tb_tournament or {}).get("rules") or {}
        total_sets, _per_set = set_layout(match, rules)
        if set_number < 1 or set_number > total_sets:
            raise HTTPException(
                status_code=422,
                detail=f"This match has {total_sets} game(s); there is no game {set_number}.",
            )

        boards = admin_db.table("boards").select("*").eq(
            "match_id", id).order("board_number").execute().data or []

        # Only a game the engine says is actually waiting on a ruling. Without
        # this the route is "award any game to anyone" -- the same hole the
        # match-level tie-break had, where a decided match could be handed to
        # the player who lost it.
        waiting = next(
            (row for row in summarise_sets(match, boards, rules)
             if row.get("setNumber") == set_number),
            None,
        )
        if not waiting or not waiting.get("needsSetTieBreak"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Game {set_number} is not waiting on a sudden-death ruling. "
                    "A game is only decided this way when it is level at the point "
                    "limit with every board played."
                ),
            )

        existing = match.get("set_tie_breaks")
        decisions = dict(existing) if isinstance(existing, dict) else {}
        decisions[str(set_number)] = {
            "method": "sudden_death",
            "winnerId": data.winner_id,
            "winnerName": match.get("player1_name") if data.winner_id == p1
                          else match.get("player2_name"),
            "reason": data.reason.strip(),
            "decidedBy": admin.get("id"),
            "decidedByName": admin.get("name"),
            "decidedAt": datetime.now(timezone.utc).isoformat(),
        }

        # Written before the recompute, so apply_set_results reads the ruling
        # it is meant to act on rather than the state before it.
        decided_match = {**match, "set_tie_breaks": decisions}
        updated = apply_set_results(decided_match, boards, rules)

        patch = {
            "set_tie_breaks": decisions,
            "player1_sets_won": updated.get("player1SetsWon", 0),
            "player2_sets_won": updated.get("player2SetsWon", 0),
            "tie_break_required": bool(updated.get("tieBreakRequired")),
        }
        if updated.get("winnerId"):
            patch.update({
                "winner_id": updated.get("winnerId"),
                "winner_name": updated.get("winnerName"),
                "status": "completed",
                "match_completed_at": datetime.now(timezone.utc).isoformat(),
            })

        try:
            admin_db.table("matches").update(patch).eq("id", id).execute()
        except Exception as e:
            if not _missing_set_tie_breaks(e):
                raise
            raise HTTPException(
                status_code=503,
                detail=("matches.set_tie_breaks is missing. Apply "
                        "db/migrations/029_official_score_finishes_and_set_ties.sql, "
                        "then record the ruling again."),
            )

        # The match carries on: open the next game's first board. Guarded on
        # 'pending' so a board already in play or already scored is untouched.
        if set_number < total_sets and not updated.get("winnerId"):
            admin_db.table("boards").update({"status": "in_progress"}).eq(
                "match_id", id).eq("set_number", set_number + 1).eq(
                "board_number", 1).eq("status", "pending").execute()

        record_audit(
            admin_db, actor=admin, action="match.set_tie_break",
            entity_type="match", entity_id=id,
            new_state={"setNumber": set_number,
                       "winnerId": data.winner_id,
                       "reason": data.reason.strip()},
            request_context={"setsWon": [patch["player1_sets_won"],
                                         patch["player2_sets_won"]]},
        )

        fresh = (admin_db.table("matches").select("*").eq(
            "id", id).execute().data or [{**match, **patch}])[0]
        return serialize_match(fresh)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Set tie-break on match %s failed: %s", id, e)
        raise HTTPException(status_code=400, detail=str(e))
