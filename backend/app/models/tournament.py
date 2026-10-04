from pydantic import BaseModel, Field, AliasGenerator, model_validator, field_validator
from pydantic.alias_generators import to_camel
from typing import Optional, List, Any, Literal
from datetime import date
import ipaddress
import re
from urllib.parse import urlparse, urlunparse


def _valid_gpay_destination(value: Optional[str]) -> Optional[str]:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if re.fullmatch(r"[6-9][0-9]{9}", cleaned) or re.fullmatch(
        r"[A-Za-z0-9._-]{2,100}@[A-Za-z0-9.-]{2,100}", cleaned
    ):
        return cleaned
    raise ValueError("Enter a 10-digit Indian GPay number or a valid UPI ID.")

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
    max_boards_per_match: int = Field(default=8, ge=1, le=8)
    target_score: int = Field(default=25, ge=1, le=50)
    queen_points: int = Field(default=3, ge=0, le=12)
    match_duration_minutes: int = Field(default=90, ge=1, le=480)
    rest_time_minutes: int = Field(default=10, ge=0, le=1440)
    tiebreaker_rules: List[str] = Field(default_factory=lambda: ["points", "net_score_difference", "board_difference", "head_to_head"])
    # Group stage (spec 68). groupCount > 1 splits the league phase into
    # balanced groups; undeclared fields are dropped by the model, so these
    # have to exist here for the setting to survive tournament creation.
    group_count: Optional[int] = Field(default=None, ge=1)
    qualifiers_per_group: Optional[int] = Field(default=None, ge=1)
    # How many league finishers reach the knockout in a league_knockout draw.
    # None keeps the engine's historical four.
    knockout_qualifiers: Optional[int] = Field(default=None, ge=2)
    # Board scoring. Associations score carrom differently, so the engine reads
    # these rather than assuming. Same caveat as above: an undeclared field is
    # dropped by the model, so it would never reach the scoring engine.
    scoring_mode: Optional[Literal["classic", "remaining_coins", "official_icf"]] = None
    coins_per_side: Optional[int] = Field(default=None, ge=1, le=20)
    queen_must_be_covered: Optional[bool] = None
    queen_award_to: Optional[Literal["coverer", "pocketer"]] = None
    tie_break: Optional[Literal["additional_board", "sudden_death", "most_board_wins", "organizer_decision"]] = None
    # Carromite format: a match is N sets of M boards, won on sets rather than
    # on total points. 1 set keeps the original flat-board behaviour.
    number_of_sets: Optional[int] = Field(default=3, ge=1, le=5)
    boards_per_set: Optional[int] = Field(default=None, ge=1, le=8)
    # What one coin is worth, and how a set is decided. Both belong in the
    # rules rather than the arithmetic: associations differ, and a set won
    # on boards can go to the other player than a set won on points.
    coin_value: Optional[int] = Field(default=None, ge=1, le=10)
    set_winner_rule: Optional[Literal["target_points", "total_points", "board_wins"]] = "target_points"
    # What the scorer is asked for on a board: 'simple' is who finished
    # and the coins left; 'detailed' adds the queen and penalties.
    board_entry_mode: Optional[Literal["simple", "detailed"]] = None

    @field_validator("tiebreaker_rules")
    @classmethod
    def valid_tiebreakers(cls, value):
        allowed = {"points", "net_score_difference", "board_difference", "head_to_head"}
        if not value or len(value) != len(set(value)) or set(value) - allowed or value[0] != "points":
            raise ValueError("Tiebreakers must start with points and contain each supported rule at most once.")
        return value

    @model_validator(mode="after")
    def valid_official_preset(self):
        # The federation variants have fixed scoring parameters. A custom
        # combination belongs to the configurable remaining_coins mode.
        if self.scoring_mode != "official_icf":
            return self
        boards = self.boards_per_set or self.max_boards_per_match
        if (self.target_score, boards) not in {(25, 8), (21, 6)}:
            raise ValueError("Official scoring supports 25 points/8 boards or 21 points/6 boards.")
        if (self.number_of_sets or 3) != 3 or self.queen_points != 3 \
                or (self.coins_per_side or 9) != 9 or (self.coin_value or 1) != 1 \
                or self.queen_must_be_covered is False \
                or self.set_winner_rule != "target_points" \
                or self.board_entry_mode == "simple":
            raise ValueError("Official scoring requires best of three games, nine coins per side, a covered three-point queen, and detailed board entry.")
        return self

class PosterConfigSchema(BaseCamelModel):
    theme_style: str = "emerald_gold"
    tagline: Optional[str] = ""
    highlights: Optional[List[str]] = []
    announcement: Optional[str] = ""
    badge_text: Optional[str] = ""
    custom_bg_url: Optional[str] = None
    poster_size: Literal["portrait", "square", "a4"] = "portrait"
    organizer_contact: Optional[str] = Field(default="", max_length=100)
    eligibility: Optional[str] = Field(default="", max_length=100)
    sponsor_text: Optional[str] = Field(default="", max_length=100)
    source_fingerprint: Optional[str] = Field(default=None, max_length=8000)
    published_at: Optional[str] = None
    public_base_url: Optional[str] = Field(default=None, max_length=300)

    @field_validator("public_base_url", mode="before")
    @classmethod
    def valid_public_base_url(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Enter the full public website address, starting with https://.")
        cleaned = value.strip()
        if not cleaned:
            return None
        # Browsers interpret backslashes and some numeric hosts differently from
        # urllib. Reject those spellings rather than publish a local QR link.
        if any(char.isspace() or ord(char) < 32 for char in cleaned) or "\\" in cleaned:
            raise ValueError("Use a public website address without spaces or backslashes.")
        try:
            parsed = urlparse(cleaned)
            host = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Enter a valid public website address.") from exc
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("The poster website must use https://.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Use a website address without login details.")
        if "?" in cleaned or "#" in cleaned:
            raise ValueError("Use the website base address without a query or # section.")
        if ":" in host or not host or "." not in host:
            raise ValueError("Use a public website hostname or public IPv4 address.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Numeric final labels can trigger browser IPv4 canonicalization,
            # including shortened, integer, octal and hexadecimal addresses.
            final_label = host.rsplit(".", 1)[-1]
            if final_label.isdigit() or re.fullmatch(r"0x[0-9a-f]+", final_label):
                raise ValueError("Use a public website hostname or full public IPv4 address.")
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise ValueError("Enter a valid public website hostname.") from exc
            if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in host.split(".")):
                raise ValueError("Enter a valid public website hostname.")
            if host.rsplit(".", 1)[-1] in {"localhost", "local", "test", "invalid", "internal", "lan"}:
                raise ValueError("Local or private website addresses cannot be shared with players.")
        else:
            if not address.is_global or address.is_multicast or address.is_reserved or address.version != 4 \
                    or address in ipaddress.ip_network("192.0.0.0/24") \
                    or address in ipaddress.ip_network("192.88.99.0/24"):
                raise ValueError("Local or reserved addresses cannot be shared with players.")
        netloc = host if port in (None, 443) else f"{host}:{port}"
        path = parsed.path.rstrip("/") + "/"
        if parsed.params:
            path = parsed.path + ";" + parsed.params.rstrip("/") + "/"
        return urlunparse(("https", netloc, path, "", "", ""))

class TournamentCreateSchema(BaseCamelModel):
    name: str
    description: Optional[str] = ""
    category: Literal["singles", "doubles", "both"] = "both"
    format: Literal["round_robin", "knockout", "league_knockout", "group_stage", "group_knockout"] = "league_knockout"
    registration_start_date: date
    registration_end_date: date
    tournament_start_date: date
    tournament_end_date: date
    venue: str
    city: str
    number_of_boards: int = Field(default=4, ge=1, le=128)
    entry_fee: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    gpay_upi_id: Optional[str] = Field(default=None, max_length=100)
    prize_pool: Optional[str] = ""
    rules: TournamentRulesSchema
    poster_config: Optional[PosterConfigSchema] = None
    status: Literal["draft"] = "draft"

    @field_validator("name", "venue", "city")
    @classmethod
    def required_text(cls, value):
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field cannot be blank.")
        return cleaned

    @field_validator("gpay_upi_id")
    @classmethod
    def valid_gpay_destination(cls, value):
        return _valid_gpay_destination(value)

    @model_validator(mode="after")
    def check_dates(self):
        if not (self.registration_start_date <= self.registration_end_date
                <= self.tournament_start_date <= self.tournament_end_date):
            raise ValueError("Tournament dates must follow registration start, registration end, tournament start, tournament end.")
        return self

class TournamentUpdateSchema(BaseCamelModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[Literal["singles", "doubles", "both"]] = None
    format: Optional[Literal["round_robin", "knockout", "league_knockout", "group_stage", "group_knockout"]] = None
    registration_start_date: Optional[date] = None
    registration_end_date: Optional[date] = None
    tournament_start_date: Optional[date] = None
    tournament_end_date: Optional[date] = None
    venue: Optional[str] = None
    city: Optional[str] = None
    number_of_boards: Optional[int] = Field(default=None, ge=1, le=128)
    entry_fee: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    gpay_upi_id: Optional[str] = Field(default=None, max_length=100)
    prize_pool: Optional[str] = None
    rules: Optional[TournamentRulesSchema] = None
    poster_config: Optional[PosterConfigSchema] = None
    status: Optional[str] = None
    schedule_published: Optional[bool] = None
    fixtures_generated: Optional[bool] = None

    @field_validator("gpay_upi_id")
    @classmethod
    def valid_gpay_destination(cls, value):
        return _valid_gpay_destination(value)

    @field_validator("name", "venue", "city")
    @classmethod
    def required_text(cls, value):
        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field cannot be blank.")
        return cleaned

class RegistrationCreateSchema(BaseCamelModel):
    type: Literal["singles", "doubles"]
    player_id: Optional[str] = None
    team_name: Optional[str] = None
    # A doubles partner can be given either as an existing profile id, or as
    # details for a partner who does not have an account yet.
    partner_id: Optional[str] = None
    partner_name: Optional[str] = None
    partner_phone: Optional[str] = None
    partner_email: Optional[str] = None
    notes: Optional[str] = None


class TournamentCancelSchema(BaseCamelModel):
    """
    Why a tournament is being called off.

    Cancelling is terminal: nothing can be scored, drawn or registered after
    it, and every participant is told. A decision like that needs to say why
    on the record, so the reason is required rather than optional -- the
    router also refuses a blank one.
    """
    reason: str


class ManualMatchSchema(BaseCamelModel):
    """
    One fixture added by hand.

    Generated draws cover everyone entered when the draw was made. A player who
    joins after that, a rematch ordered by the referee, or a play-off the format
    does not produce all need a single match created on its own without
    redrawing the tournament and losing the results already recorded.
    """
    stage: Literal["league", "knockout"] = "league"
    round_name: str = "League"            # what the round is called on screen
    group: Optional[str] = None            # required for new entrants in a grouped league
    player1_id: str
    player2_id: str
    board_number: Optional[int] = Field(default=None, ge=1, le=128)
    scheduled_date: Optional[str] = None
    scheduled_time: Optional[str] = None
