"""
Server-side score validation (spec 70, 93).

The frontend is never the authoritative source for an official result, so every
submitted score is checked against the tournament's configured carrom rules
before it is allowed near the database.
"""
from typing import Any, Dict, Optional
from fastapi import HTTPException

# AICF: 19 carrom men (9 white, 9 black, 1 queen). A single board cannot
# mathematically yield more than this to one side.
ABSOLUTE_MAX_BOARD_POINTS = 60


def validate_board_score(
    p1_score: int,
    p2_score: int,
    match: Dict[str, Any],
    queen_claimed_by: Optional[str] = None,
) -> None:
    """Raises 422 with a user-readable message if the score is not legal."""
    if p1_score < 0 or p2_score < 0:
        raise HTTPException(status_code=422, detail="Board scores cannot be negative.")

    target = match.get("target_points") or ABSOLUTE_MAX_BOARD_POINTS
    ceiling = max(int(target), ABSOLUTE_MAX_BOARD_POINTS)

    for label, score in (("Player 1", p1_score), ("Player 2", p2_score)):
        if score > ceiling:
            raise HTTPException(
                status_code=422,
                detail=f"{label}'s score of {score} exceeds the maximum possible board score ({ceiling}).",
            )

    # In carrom only one side pockets the remaining men, so both players cannot
    # finish a board on the target score.
    if p1_score >= int(target) and p2_score >= int(target):
        raise HTTPException(
            status_code=422,
            detail=f"Both players cannot reach the target score of {target} on the same board.",
        )

    if queen_claimed_by not in (None, "none", "player1", "player2"):
        raise HTTPException(
            status_code=422,
            detail="queenClaimedBy must be one of: player1, player2, none.",
        )

    # A queen claim is worth points, so the claimant cannot have scored nothing.
    if queen_claimed_by == "player1" and p1_score == 0:
        raise HTTPException(
            status_code=422,
            detail="Player 1 cannot be credited with the queen on a board they scored 0 on.",
        )
    if queen_claimed_by == "player2" and p2_score == 0:
        raise HTTPException(
            status_code=422,
            detail="Player 2 cannot be credited with the queen on a board they scored 0 on.",
        )
