from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.models.tournament import BaseCamelModel

class BoardScoreSchema(BaseCamelModel):
    board_number: int
    set_number: Optional[int] = None
    status: str = "pending"  # "pending", "in_progress", "completed"
    player1_score: int = 0
    player2_score: int = 0

    # A correction restates what the umpire saw, exactly as the original
    # submission did. Without these a correction had nothing to re-score from
    # and could only rewrite the board with what was already on it.
    board_winner: Optional[str] = None
    p1_coins_pocketed: Optional[int] = None
    p2_coins_pocketed: Optional[int] = None
    coins_remaining_with: Optional[str] = None
    coins_remaining: Optional[int] = None
    queen_pocketed_by: Optional[str] = None
    queen_covered_by: Optional[str] = None
    p1_penalty: Optional[int] = None
    p2_penalty: Optional[int] = None

    queen_claimed_by: Optional[str] = "none"  # "player1", "player2", "none"
    queen_covered: Optional[bool] = False
    fouls_player1: Optional[int] = 0
    fouls_player2: Optional[int] = 0
    white_coins_pocketed: Optional[int] = 0
    black_coins_pocketed: Optional[int] = 0
    duration_minutes: Optional[float] = None
    completed_at: Optional[datetime] = None
    notes: Optional[str] = None

class ScoreAuditLogSchema(BaseCamelModel):
    id: str
    timestamp: datetime
    admin_name: str
    board_number: int
    previous_score: Dict[str, int]  # {"player1": score, "player2": score}
    new_score: Dict[str, int]
    reason: str

class MatchUpdateSchema(BaseCamelModel):
    status: Optional[str] = None  # "scheduled", "live", "paused", "completed"
    board_number: Optional[int] = None
    scheduled_date: Optional[str] = None
    scheduled_time: Optional[str] = None
    timer_started_at: Optional[int] = None
    timer_elapsed_seconds: Optional[int] = None
    is_timer_running: Optional[bool] = None
    match_completed_at: Optional[datetime] = None
    winner_id: Optional[str] = None
    winner_name: Optional[str] = None
    result_confirmed: Optional[bool] = None
    result_confirmed_at: Optional[datetime] = None
    player1_board_wins: Optional[int] = None
    player2_board_wins: Optional[int] = None
    player1_total_points: Optional[int] = None
    player2_total_points: Optional[int] = None

class ScoreSubmitSchema(BaseCamelModel):
    """
    What the umpire saw on one board.

    Every field is an independent observation. Naming a winner says nothing
    about the queen, and taking the queen says nothing about who won — a player
    can lose the board and still be credited with covering the queen.
    """
    p1_score: int
    p2_score: int
    # Board numbers restart each set, so a board is (set, number).
    set_number: Optional[int] = None

    # --- remaining-coins scoring (the fields below are all optional so an
    #     older client submitting only p1_score/p2_score still works) ---
    board_winner: Optional[str] = None          # 'player1' | 'player2' | 'none'
    p1_coins_pocketed: Optional[int] = None
    p2_coins_pocketed: Optional[int] = None
    coins_remaining_with: Optional[str] = None  # whose coins are left on the board
    coins_remaining: Optional[int] = None
    queen_pocketed_by: Optional[str] = None
    queen_covered_by: Optional[str] = None      # may be the opponent
    p1_penalty: Optional[int] = 0
    p2_penalty: Optional[int] = 0

    # Legacy spelling, still accepted.
    queen_claimed_by: Optional[str] = "none"  # "player1", "player2", "none"
    queen_covered: Optional[bool] = False
    audit_reason: Optional[str] = "Board score finalized"


class TossSchema(BaseCamelModel):
    """The umpire's record of the toss before the first board is played."""
    coin_result: Optional[str] = None          # 'black' | 'white'
    toss_winner_id: Optional[str] = None       # profile id (singles) or team id
    toss_winner_name: Optional[str] = None
    choice: str = "strike"                     # 'strike' | 'side'


class WalkoverSchema(BaseCamelModel):
    """
    A result decided off the board: a no-show, a retirement, or a concession.

    The winner is required and the reason is required, because a match nobody
    played needs to say why on the record — the standings cannot distinguish it
    from a played win otherwise.
    """
    winner_id: str
    reason: str


class MatchSidesSchema(BaseCamelModel):
    """
    Who plays which coin, and which way round the board is drawn.

    Colour belongs to the player id. `sides_swapped` moves the display only —
    player1 stays player1 however the umpire is standing, so every reference
    recorded against them survives the swap.
    """
    player1_color: Optional[str] = None   # 'black' | 'white'
    player2_color: Optional[str] = None
    sides_swapped: Optional[bool] = None
    table_number: Optional[int] = None
    referee_id: Optional[str] = None
