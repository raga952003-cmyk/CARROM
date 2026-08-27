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
    allow_scoreless_queen: bool = False,
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

    # Judged on the coin count the scorer entered, before the queen is added:
    # a board won 21-3 with the queen becomes 24-3, which must not be treated
    # as both sides reaching the target.
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

    # A queen on a board where nothing at all was entered is a slip — the scorer
    # picked the queen and forgot the coins. A queen on a board where the OTHER
    # side scored is a real result: the loser can be the one who covered it, and
    # under classic scoring their board score is then just the queen. Rejecting
    # that made an ordinary carrom board impossible to record.
    if not allow_scoreless_queen:
        if queen_claimed_by in ("player1", "player2") and p1_score == 0 and p2_score == 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "The queen is credited to a player but neither side scored. "
                    "Enter the coins each player pocketed, or set the queen to none."
                ),
            )
