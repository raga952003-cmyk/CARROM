from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.models.tournament import BaseCamelModel

class BoardScoreSchema(BaseCamelModel):
    board_number: int
    status: str = "pending"  # "pending", "in_progress", "completed"
    player1_score: int = 0
    player2_score: int = 0
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
    p1_score: int
    p2_score: int
    queen_claimed_by: Optional[str] = "none"  # "player1", "player2", "none"
    queen_covered: Optional[bool] = False
    audit_reason: Optional[str] = "Board score finalized"
