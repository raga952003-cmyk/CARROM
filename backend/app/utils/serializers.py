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
                     include_contact: bool = True) -> Optional[Dict[str, Any]]:
    """
    A player profile.

    `include_contact=False` drops phone and email. The public directory used
    to return whole profile rows, which published every participant's mobile
    number and email address to anyone who could reach the API.
    """
    if not row:
        return None
    if include_contact:
        return camelize(row)
    return camelize({k: v for k, v in row.items() if k not in PRIVATE_PLAYER_FIELDS})


def serialize_team(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Teams carry hydrated player1/player2 objects when the caller joined them."""
    if not row:
        return None
    team = camelize(row)
    if row.get("player1"):
        team["player1"] = serialize_player(row["player1"])
    if row.get("player2"):
        team["player2"] = serialize_player(row["player2"])
    return team


def serialize_board(row: Dict[str, Any]) -> Dict[str, Any]:
    return camelize(row)


def serialize_registration(row: Dict[str, Any]) -> Dict[str, Any]:
    reg = camelize(row)
    reg["player"] = serialize_player(row.get("player"))
    reg["team"] = serialize_team(row.get("team"))
    return reg


def serialize_audit_log(row: Dict[str, Any]) -> Dict[str, Any]:
    return camelize(row)


def serialize_match(
    row: Dict[str, Any],
    boards: Optional[List[Dict[str, Any]]] = None,
    audit_logs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    match = camelize(row)
    match["boards"] = [serialize_board(b) for b in (boards or [])]
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
