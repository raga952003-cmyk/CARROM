from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_db, get_admin_db
from app.models.match import (
    MatchUpdateSchema, ScoreSubmitSchema, BoardScoreSchema, TossSchema,
    MatchSidesSchema, WalkoverSchema,
)
from app.utils.security import get_user_profile, verify_admin
from app.services.scoring_engine import (
    recalculate_match_scores, apply_queen_points, board_result, scoring_mode,
    apply_set_results, summarise_sets, set_layout,
)
from app.services.notification_service import fan_out_notification, resolve_tournament_audience
from app.services.transaction_service import apply_board_result, confirm_match_result
from app.services.qualification import try_auto_promote
from app.services.access_control import require_tournament_access
from app.services.audit_service import record_audit
from app.services.state_machine import validate_match_transition, assert_match_scorable
from app.services.score_validation import validate_board_score
from app.utils.serializers import serialize_board, serialize_match
from app.utils.idempotency import IdempotencyGuard, get_idempotency_key
from typing import Dict, Any
from datetime import datetime, timezone
import json
import time

router = APIRouter(prefix="/matches", tags=["matches"])

def tournament_rules(admin_db, tournament_id: str) -> dict:
    """The tournament's scoring rules, for the queen value."""
    if not tournament_id:
        return {}
    rows = admin_db.table("tournaments").select("rules").eq(
        "id", tournament_id).execute().data
    return (rows[0].get("rules") or {}) if rows else {}


def prev_match_tournament(admin_db, match_id: str) -> str:
    rows = admin_db.table("matches").select("tournament_id").eq(
        "id", match_id).execute().data
    return rows[0]["tournament_id"] if rows else ""


def _authorise_match(admin_db, match_id: str, admin, action: str):
    """Resolve a match to its tournament and authorise the caller for `action`."""
    rows = admin_db.table("matches").select("*").eq("id", match_id).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Match not found.")
    match = rows[0]
    require_tournament_access(admin_db, match["tournament_id"], admin, action)
    return match




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
        current = _authorise_match(admin_db, id, admin, "match.start")
        validate_match_transition(current.get("status"), "live")

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
        _authorise_match(admin_db, id, admin, "match.add_board")

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
        _authorise_match(admin_db, id, admin, "match.add_board")

        existing = admin_db.table("boards").select("*").eq(
            "match_id", id).order("board_number").execute().data or []
        played = [b for b in existing if b.get("status") == "completed"
                  or (b.get("player1_score") or 0) or (b.get("player2_score") or 0)]

        if boards < len(played):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This match already has {} board(s) with a result on them, so it "
                    "cannot be shortened to {}."
                ).format(len(played), boards),
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
        _authorise_match(admin_db, id, admin, "match.add_board")

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
        # Bring the configured length back in line with what is actually there,
        # so the win condition matches the match being played.
        admin_db.table("matches").update({"max_boards": remaining}).eq("id", id).execute()

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
):
    admin_db = get_admin_db()
    try:
        # Correcting a board is scoring, and was the one scoring path that never
        # checked. Being an admin was enough to rewrite any board on anyone's
        # tournament, which is precisely what the ownership model exists to stop.
        _authorise_match(admin_db, id, admin, "match.score")

        # Fetch previous board score for audit log
        prev_board = admin_db.table("boards").select("*").eq("match_id", id).eq("board_number", board_number).execute()
        if not prev_board.data:
            raise HTTPException(status_code=404, detail="Board not found")
        pb = prev_board.data[0]

        if pb.get("locked") and not override:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Board {} is confirmed. Re-submitting would quietly rewrite a played "
                    "game, so an override and a reason are required to change it."
                ).format(board_number),
            )
        overriding = bool(pb.get("locked") and override)

        # Update board score
        # Corrections go through the same rule as the original submission, so a
        # fixed score is not left missing the queen — or, under remaining-coins
        # scoring, silently re-scored under the classic formula.
        corrected_rules = (tournament_rules(admin_db, prev_match_tournament(admin_db, id)) or {})
        corrected_mode = scoring_mode(corrected_rules)

        if corrected_mode == "remaining_coins":
            # A correction restates the observations, so it is scored from them
            # rather than from the two numbers, which are outputs not inputs.
            # Anything the correction does not mention keeps what the board
            # already had; `is None` rather than `or`, so a deliberate 0 sticks.
            def restated(field, stored, fallback=None):
                value = getattr(data, field, None)
                if value is not None:
                    return value
                return pb.get(stored, fallback) if pb.get(stored) is not None else fallback

            outcome = board_result(
                winner=restated("board_winner", "board_winner", "none"),
                p1_coins_pocketed=restated("p1_coins_pocketed", "p1_coins_pocketed"),
                p2_coins_pocketed=restated("p2_coins_pocketed", "p2_coins_pocketed"),
                coins_remaining_with=restated("coins_remaining_with", "coins_remaining_with"),
                coins_remaining=restated("coins_remaining", "coins_remaining"),
                queen_pocketed_by=restated("queen_pocketed_by", "queen_pocketed_by")
                                  or data.queen_claimed_by,
                queen_covered_by=restated("queen_covered_by", "queen_covered_by"),
                p1_penalty=restated("p1_penalty", "p1_penalty", 0) or 0,
                p2_penalty=restated("p2_penalty", "p2_penalty", 0) or 0,
                rules=corrected_rules,
            )
            c_p1, c_p2 = outcome["player1_score"], outcome["player2_score"]
        else:
            c_p1, c_p2, _ = apply_queen_points(
                data.player1_score, data.player2_score,
                data.queen_claimed_by, data.queen_covered, corrected_rules,
            )

        update_payload = {
            "player1_score": c_p1,
            "player2_score": c_p2,
            "status": data.status,
            "board_winner": data.board_winner or "none",
            "queen_claimed_by": data.queen_claimed_by,
            "queen_covered": data.queen_covered,
            "fouls_player1": data.fouls_player1,
            "fouls_player2": data.fouls_player2,
            "white_coins_pocketed": data.white_coins_pocketed,
            "black_coins_pocketed": data.black_coins_pocketed,
            "notes": data.notes
        }

        if corrected_mode == "remaining_coins":
            # Store what the correction observed, not just what it scored, so a
            # second correction reads the current board rather than the original.
            update_payload.update({
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

        if not board_detail_available(admin_db):
            for key in _BOARD_DETAIL_COLUMNS:
                update_payload.pop(key, None)
        if data.status == "completed":
            update_payload["completed_at"] = datetime.utcnow().isoformat()

        res = admin_db.table("boards").update(update_payload).eq("match_id", id).eq("board_number", board_number).execute()
        
        # Create audit log
        audit_payload = {
            "match_id": id,
            "admin_id": admin["id"],
            "admin_name": admin["name"],
            "board_number": board_number,
            "previous_score": {"player1": pb.get("player1_score", 0), "player2": pb.get("player2_score", 0)},
            "new_score": {"player1": c_p1, "player2": c_p2},
            "reason": ("OVERRIDE of a confirmed board: " + reason) if overriding else reason,
        }
        admin_db.table("score_audit_logs").insert(audit_payload).execute()
        
        # Recalculate match score
        boards = admin_db.table("boards").select("*").eq("match_id", id).execute().data
        match_data = admin_db.table("matches").select("*").eq("id", id).execute().data[0]
        
        updated_match = recalculate_match_scores(match_data, boards, corrected_rules)
        
        # Persist updated match scores
        admin_db.table("matches").update({
            "player1_board_wins": updated_match["player1BoardWins"],
            "player2_board_wins": updated_match["player2BoardWins"],
            "player1_total_points": updated_match["player1TotalPoints"],
            "player2_total_points": updated_match["player2TotalPoints"],
            "status": updated_match["status"],
            "winner_id": updated_match["winnerId"],
            "winner_name": updated_match["winnerName"],
            "match_completed_at": updated_match.get("matchCompletedAt")
        }).eq("id", id).execute()

        return res.data[0]
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
        match_data = _authorise_match(admin_db, id, admin, "match.score")
        assert_match_scorable(match_data)

        boards = admin_db.table("boards").select("*").eq("match_id", id).order("board_number").execute().data or []

        # Board numbers restart in each set, so the set has to be named or the
        # board is ambiguous. Unset means the match is not played in sets.
        set_number = data.set_number or 1
        def is_target(b):
            return (b["board_number"] == board_number
                    and (b.get("set_number") or 1) == set_number)

        if not any(is_target(b) for b in boards):
            raise HTTPException(
                status_code=404,
                detail="Board {} of set {} does not exist on this match.".format(
                    board_number, set_number),
            )

        rules = (tournament_rules(admin_db, match_data["tournament_id"]) or {})
        mode = scoring_mode(rules)

        validate_board_score(
            data.p1_score, data.p2_score, match_data, data.queen_claimed_by,
            allow_scoreless_queen=(mode == "remaining_coins"),
        )

        if mode == "remaining_coins":
            # The umpire's observations are scored server-side. Each one is
            # taken as given: the winner, the queen and the coins left on the
            # board are three separate facts and none is inferred from another.
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
                p1_penalty=data.p1_penalty or 0,
                p2_penalty=data.p2_penalty or 0,
                rules=rules,
            )
            p1_final = outcome["player1_score"]
            p2_final = outcome["player2_score"]
            queen_note = (
                f"base {outcome['base_points']} + queen {outcome['queen_bonus']} "
                f"to {outcome['queen_awarded_to']}"
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
                "board_winner": data.board_winner or "none",
                "queen_claimed_by": data.queen_claimed_by,
                "queen_covered": data.queen_covered,
                "completed_at": datetime.utcnow().isoformat(),
            }

        # Recompute the match from the board set as it will be after this write.
        projected = [
            {**b, **board_patch} if is_target(b) else b
            for b in boards
        ]
        total_sets, _ = set_layout(match_data, rules)
        updated_match = (apply_set_results(match_data, projected, rules) if total_sets > 1
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
        if mode == "remaining_coins":
            # An all-boards-played draw needs a human decision, so say so on
            # the match rather than quietly leaving it without a winner.
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
        has_next = any(b["board_number"] == next_board_number
                       and (b.get("set_number") or 1) == set_number
                       for b in boards)
        if not has_next and total_sets > 1:
            # End of a set: the following set opens at its first board.
            if any(b["board_number"] == 1 and (b.get("set_number") or 1) == set_number + 1
                   for b in boards):
                admin_db.table("boards").update({"status": "in_progress"}).eq(
                    "match_id", id).eq("set_number", set_number + 1).eq(
                    "board_number", 1).eq("status", "pending").execute()

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
        )

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
        m = _authorise_match(admin_db, id, admin, "match.confirm")

        if not m.get("winner_id") and m.get("status") != "completed":
            raise HTTPException(
                status_code=409,
                detail="This match has no decided winner yet. Finish the remaining boards first.",
            )

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
        match = _authorise_match(admin_db, id, admin, "match.walkover")

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

        rules = tournament_rules(admin_db, match.get("tournament_id")) or {}
        max_boards = match.get("max_boards") or rules.get("maxBoardsPerMatch") or 3
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
