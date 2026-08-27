from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_db, get_admin_db
from app.models.match import MatchUpdateSchema, ScoreSubmitSchema, BoardScoreSchema, TossSchema
from app.utils.security import get_user_profile, verify_admin
from app.services.scoring_engine import (
    recalculate_match_scores, apply_queen_points, board_result, scoring_mode,
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
_board_detail_available: Dict[str, bool] = {}


def board_detail_available(admin_db) -> bool:
    """Whether migration 005 has been applied. Probed once per process."""
    if "value" not in _board_detail_available:
        try:
            admin_db.table("boards").select("board_winner").limit(1).execute()
            _board_detail_available["value"] = True
        except Exception:
            _board_detail_available["value"] = False
    return _board_detail_available["value"]


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
        
        # Increase max boards count on match
        admin_db.table("matches").update({"max_boards": count + 1}).eq("id", id).execute()
        return serialize_board(res.data[0])
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
            outcome = board_result(
                winner=pb.get("board_winner") or "none",
                p1_coins_pocketed=pb.get("p1_coins_pocketed") or 0,
                p2_coins_pocketed=pb.get("p2_coins_pocketed") or 0,
                coins_remaining_with=pb.get("coins_remaining_with"),
                coins_remaining=pb.get("coins_remaining"),
                queen_pocketed_by=data.queen_claimed_by or pb.get("queen_pocketed_by"),
                queen_covered_by=pb.get("queen_covered_by"),
                p1_penalty=data.fouls_player1 or pb.get("p1_penalty") or 0,
                p2_penalty=data.fouls_player2 or pb.get("p2_penalty") or 0,
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
            "queen_claimed_by": data.queen_claimed_by,
            "queen_covered": data.queen_covered,
            "fouls_player1": data.fouls_player1,
            "fouls_player2": data.fouls_player2,
            "white_coins_pocketed": data.white_coins_pocketed,
            "black_coins_pocketed": data.black_coins_pocketed,
            "notes": data.notes
        }
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
        if not any(b["board_number"] == board_number for b in boards):
            raise HTTPException(status_code=404, detail=f"Board {board_number} does not exist on this match.")

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
                p1_coins_pocketed=data.p1_coins_pocketed or 0,
                p2_coins_pocketed=data.p2_coins_pocketed or 0,
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
                "queen_claimed_by": data.queen_claimed_by,
                "queen_covered": data.queen_covered,
                "completed_at": datetime.utcnow().isoformat(),
            }

        # Recompute the match from the board set as it will be after this write.
        projected = [
            {**b, **board_patch} if b["board_number"] == board_number else b
            for b in boards
        ]
        updated_match = recalculate_match_scores(match_data, projected, rules)

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
        has_next = any(b["board_number"] == next_board_number for b in boards)

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
