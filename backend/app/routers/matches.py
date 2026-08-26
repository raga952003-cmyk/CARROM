from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_db, get_admin_db
from app.models.match import MatchUpdateSchema, ScoreSubmitSchema, BoardScoreSchema
from app.utils.security import get_user_profile, verify_admin
from app.services.scoring_engine import recalculate_match_scores
from app.services.notification_service import fan_out_notification, resolve_tournament_audience
from app.services.transaction_service import apply_board_result, confirm_match_result
from app.services.qualification import try_auto_promote
from app.services.state_machine import validate_match_transition, assert_match_scorable
from app.services.score_validation import validate_board_score
from app.utils.serializers import serialize_board, serialize_match
from app.utils.idempotency import IdempotencyGuard, get_idempotency_key
from typing import Dict, Any
from datetime import datetime
import json

router = APIRouter(prefix="/matches", tags=["matches"])

@router.post("/{id}/start")
async def start_match(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        current = admin_db.table("matches").select("*").eq("id", id).execute().data
        if not current:
            raise HTTPException(status_code=404, detail="Match not found.")
        validate_match_transition(current[0].get("status"), "live")

        now_ms = int(datetime.utcnow().timestamp() * 1000)
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
        rows = admin_db.table("matches").select("*").eq("id", id).execute().data
        if not rows:
            raise HTTPException(status_code=404, detail="Match not found.")
        m = rows[0]
        validate_match_transition(m.get("status"), "paused")
        elapsed = m.get("timer_elapsed_seconds", 0)
        started_at = m.get("timer_started_at")
        
        if m.get("is_timer_running") and started_at:
            now_ms = int(datetime.utcnow().timestamp() * 1000)
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
        current = admin_db.table("matches").select("*").eq("id", id).execute().data
        if not current:
            raise HTTPException(status_code=404, detail="Match not found.")
        validate_match_transition(current[0].get("status"), "live")

        now_ms = int(datetime.utcnow().timestamp() * 1000)
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
async def update_board(id: str, board_number: int, data: BoardScoreSchema, reason: str = Query("Scorer update"), admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        # Fetch previous board score for audit log
        prev_board = admin_db.table("boards").select("*").eq("match_id", id).eq("board_number", board_number).execute()
        if not prev_board.data:
            raise HTTPException(status_code=404, detail="Board not found")
        pb = prev_board.data[0]

        # Update board score
        update_payload = {
            "player1_score": data.player1_score,
            "player2_score": data.player2_score,
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
            "new_score": {"player1": data.player1_score, "player2": data.player2_score},
            "reason": reason
        }
        admin_db.table("score_audit_logs").insert(audit_payload).execute()
        
        # Recalculate match score
        boards = admin_db.table("boards").select("*").eq("match_id", id).execute().data
        match_data = admin_db.table("matches").select("*").eq("id", id).execute().data[0]
        
        updated_match = recalculate_match_scores(match_data, boards)
        
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
        match_rows = admin_db.table("matches").select("*").eq("id", id).execute().data
        if not match_rows:
            raise HTTPException(status_code=404, detail="Match not found.")
        match_data = match_rows[0]
        assert_match_scorable(match_data)

        boards = admin_db.table("boards").select("*").eq("match_id", id).order("board_number").execute().data or []
        if not any(b["board_number"] == board_number for b in boards):
            raise HTTPException(status_code=404, detail=f"Board {board_number} does not exist on this match.")

        validate_board_score(data.p1_score, data.p2_score, match_data, data.queen_claimed_by)

        board_patch = {
            "player1_score": data.p1_score,
            "player2_score": data.p2_score,
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
        updated_match = recalculate_match_scores(match_data, projected)

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
                "new_score": {"player1": data.p1_score, "player2": data.p2_score},
                "reason": data.audit_reason,
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
        match_res = admin_db.table("matches").select("*").eq("id", id).execute()
        if not match_res.data:
            raise HTTPException(status_code=404, detail="Match not found")
        m = match_res.data[0]

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
