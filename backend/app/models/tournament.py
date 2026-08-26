from pydantic import BaseModel, Field, AliasGenerator
from pydantic.alias_generators import to_camel
from typing import Optional, List, Any
from datetime import date

class BaseCamelModel(BaseModel):
    model_config = {
        "alias_generator": to_camel,
        "populate_by_name": True,
        "from_attributes": True
    }

class TournamentRulesSchema(BaseCamelModel):
    points_for_win: int = 2
    points_for_draw: int = 1
    points_for_loss: int = 0
    max_boards_per_match: int = 3
    target_score: int = 29
    queen_points: int = 3
    match_duration_minutes: int = 30
    rest_time_minutes: int = 10
    tiebreaker_rules: List[str] = ["points", "board_difference", "net_score_difference", "head_to_head"]
    # Group stage (spec 68). groupCount > 1 splits the league phase into
    # balanced groups; undeclared fields are dropped by the model, so these
    # have to exist here for the setting to survive tournament creation.
    group_count: Optional[int] = None
    qualifiers_per_group: Optional[int] = None

class PosterConfigSchema(BaseCamelModel):
    theme_style: str = "emerald_gold"
    tagline: Optional[str] = ""
    highlights: Optional[List[str]] = []
    announcement: Optional[str] = ""
    badge_text: Optional[str] = ""
    custom_bg_url: Optional[str] = None

class TournamentCreateSchema(BaseCamelModel):
    name: str
    description: Optional[str] = ""
    category: str = "both"  # "singles", "doubles", "both"
    format: str = "league_knockout"  # "round_robin", "knockout", "league_knockout"
    registration_start_date: date
    registration_end_date: date
    tournament_start_date: date
    tournament_end_date: date
    venue: str
    city: str
    number_of_boards: int = 4
    entry_fee: float = 0.0
    prize_pool: Optional[str] = ""
    rules: TournamentRulesSchema
    poster_config: Optional[PosterConfigSchema] = None
    status: Optional[str] = "draft"

class TournamentUpdateSchema(BaseCamelModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    format: Optional[str] = None
    registration_start_date: Optional[date] = None
    registration_end_date: Optional[date] = None
    tournament_start_date: Optional[date] = None
    tournament_end_date: Optional[date] = None
    venue: Optional[str] = None
    city: Optional[str] = None
    number_of_boards: Optional[int] = None
    entry_fee: Optional[float] = None
    prize_pool: Optional[str] = None
    rules: Optional[TournamentRulesSchema] = None
    poster_config: Optional[PosterConfigSchema] = None
    status: Optional[str] = None
    schedule_published: Optional[bool] = None
    fixtures_generated: Optional[bool] = None

class RegistrationCreateSchema(BaseCamelModel):
    type: str  # "singles" or "doubles"
    player_id: Optional[str] = None
    team_name: Optional[str] = None
    # A doubles partner can be given either as an existing profile id, or as
    # details for a partner who does not have an account yet.
    partner_id: Optional[str] = None
    partner_name: Optional[str] = None
    partner_phone: Optional[str] = None
    partner_email: Optional[str] = None
    notes: Optional[str] = None
