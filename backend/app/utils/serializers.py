"""
Response serializers.

Supabase returns snake_case database rows, but the frontend `types/tournament.ts`
contracts are camelCase. These helpers convert at the response boundary so the
API speaks one consistent dialect. Routers keep reading raw snake_case rows
internally; only what goes over the wire is converted.
"""
from typing import Any, Dict, List, Optional
import re

_SNAKE_RE = re.compile(r"_([a-z0-9])")


def to_camel(key: str) -> str:
    """player1_score -> player1Score, fouls_player1 -> foulsPlayer1."""
    return _SNAKE_RE.sub(lambda m: m.group(1).upper(), key)


def camelize(value: Any) -> Any:
    """Recursively convert dict keys from snake_case to camelCase."""
    if isinstance(value, dict):
        return {to_camel(k): camelize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [camelize(v) for v in value]
    return value


# Contact details are never part of a public listing.
PRIVATE_PLAYER_FIELDS = {"phone", "email"}


def serialize_player(row: Optional[Dict[str, Any]],
                     include_contact: bool = False) -> Optional[Dict[str, Any]]:
    """
    A player profile. Contact details are withheld unless asked for.

    The default used to be True, so every path leaked unless it remembered not
    to -- and the registration paths did not remember. Migration 011 closed
    this at the database, but these routes read through the service-role
    client, which bypasses RLS entirely, so an anonymous caller could still
    read twenty email addresses and a mobile number out of
    /api/tournaments/{id}/registrations and out of the public board's own
    payload. A default that has to be overridden to be safe is the wrong way
    round.
    """
    if not row:
        return None
    if include_contact:
        return camelize(row)
    return camelize({k: v for k, v in row.items() if k not in PRIVATE_PLAYER_FIELDS})


def serialize_team(row: Optional[Dict[str, Any]],
                   include_contact: bool = False) -> Optional[Dict[str, Any]]:
    """Teams carry hydrated player1/player2 objects when the caller joined them."""
    if not row:
        return None
    team = camelize(row)
    if row.get("player1"):
        team["player1"] = serialize_player(row["player1"], include_contact)
    if row.get("player2"):
        team["player2"] = serialize_player(row["player2"], include_contact)
    return team


def serialize_board(row: Dict[str, Any]) -> Dict[str, Any]:
    return camelize(row)


def serialize_registration(row: Dict[str, Any],
                           include_contact: bool = False) -> Dict[str, Any]:
    """
    An entry, with the entrant hydrated.

    An organiser needs a way to ring an entrant, so contact details are
    available -- but only to an admin who asked for them, never to whoever
    happens to open the public board.
    """
    reg = camelize(row)
    reg["player"] = serialize_player(row.get("player"), include_contact)
    reg["team"] = serialize_team(row.get("team"), include_contact)
    return reg


def serialize_audit_log(row: Dict[str, Any]) -> Dict[str, Any]:
    return camelize(row)


def board_has_play(b: Dict[str, Any]) -> bool:
    """Whether a board carries anything worth sending over the wire."""
    return bool(
        b.get("status") not in (None, "pending")
        or (b.get("player1_score") or 0)
        or (b.get("player2_score") or 0)
        or (b.get("board_winner") or "none") != "none"
    )


def serialize_match(
    row: Dict[str, Any],
    boards: Optional[List[Dict[str, Any]]] = None,
    audit_logs: Optional[List[Dict[str, Any]]] = None,
    boards_with_play_only: bool = False,
) -> Dict[str, Any]:
    match = camelize(row)
    all_boards = boards or []
    # boardCount is what the client rebuilds the full list from, and it counts
    # the rows that exist rather than max_boards -- a tie-break board is a real
    # board beyond the configured length.
    match["boardCount"] = len(all_boards)
    if boards_with_play_only:
        # An unplayed board is eight identical zeroes. Sending 1520 of them made
        # the tournament payload 1.4 MB and took 5.7 seconds, on every load and
        # again on every realtime change -- so every board an umpire scored made
        # every other screen re-download the whole draw. The client fills the
        # gaps from boardCount; nothing addresses a board by id.
        all_boards = [b for b in all_boards if board_has_play(b)]
    match["boards"] = [serialize_board(b) for b in all_boards]
    match["auditHistory"] = [serialize_audit_log(a) for a in (audit_logs or [])]
    # The UI treats these as required; DB nulls would break `.length`/comparisons.
    match["scheduledDate"] = row.get("scheduled_date") or ""
    match["scheduledTime"] = row.get("scheduled_time") or ""
    match["roundName"] = row.get("round_name") or ""
    return match


def serialize_tournament(
    row: Dict[str, Any],
    registrations: Optional[List[Dict[str, Any]]] = None,
    matches: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    tournament = camelize(row)
    tournament["registrations"] = registrations or []
    tournament["matches"] = matches or []
    # `types/tournament.ts` spells this `scheduledPublished`; keep both so the
    # camelized column name and the UI contract agree.
    tournament["scheduledPublished"] = row.get("schedule_published", False)
    tournament["fixturesGenerated"] = row.get("fixtures_generated", False)
    tournament["posterConfig"] = row.get("poster_config") or {}
    return tournament


def serialize_notification(row: Dict[str, Any]) -> Dict[str, Any]:
    notification = camelize(row)
    notification["read"] = row.get("read", False)
    notification["timestamp"] = row.get("created_at")
    tournament = row.get("tournament")
    if tournament:
        notification["tournamentName"] = tournament.get("name")
    notification.pop("tournament", None)
    return notification
