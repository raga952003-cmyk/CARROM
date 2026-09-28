"""One schedule check shared by schedule previews, fixture edits and publication."""
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional


def _start(match: Dict[str, Any]) -> Optional[datetime]:
    day, clock = match.get("scheduled_date"), match.get("scheduled_time")
    if not day or not clock:
        return None
    for pattern in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{str(day)[:10]} {clock}", pattern)
        except ValueError:
            pass
    return None


def detect_schedule_conflicts(
    matches: List[Dict[str, Any]],
    team_members: Optional[Dict[str, List[str]]] = None,
    duration_minutes: int = 30,
    rest_minutes: int = 10,
    *,
    tournament_start_date: Optional[str] = None,
    tournament_end_date: Optional[str] = None,
    number_of_boards: Optional[int] = None,
) -> List[Dict[str, Any]]:
    team_members = team_members or {}
    conflicts: List[Dict[str, Any]] = []
    active = [m for m in matches if m.get("status") != "cancelled"]
    by_id = {str(m["id"]): m for m in active if m.get("id")}
    scheduled = []
    first_day = date.fromisoformat(str(tournament_start_date)[:10]) if tournament_start_date else None
    last_day = date.fromisoformat(str(tournament_end_date)[:10]) if tournament_end_date else None

    for match in active:
        start = _start(match)
        board = match.get("board_number")
        if start is None or not board:
            conflicts.append({
                "type": "unscheduled", "matchNumbers": [match.get("match_number")],
                "detail": f"Match {match.get('match_number')} needs a valid date, time and board.",
            })
            continue
        if number_of_boards is not None and not 1 <= int(board) <= number_of_boards:
            conflicts.append({
                "type": "board_out_of_range", "matchNumbers": [match.get("match_number")],
                "detail": f"Match {match.get('match_number')} uses board {board}; this venue has boards 1 to {number_of_boards}.",
            })
        if ((first_day and start.date() < first_day)
                or (last_day and start + timedelta(minutes=duration_minutes)
                    > datetime.combine(last_day + timedelta(days=1), datetime.min.time()))):
            conflicts.append({
                "type": "outside_tournament_dates", "matchNumbers": [match.get("match_number")],
                "detail": f"Match {match.get('match_number')} does not fit within the tournament dates.",
            })
        sides = [match.get("player1_id"), match.get("player2_id")]
        people = set()
        for side in sides:
            if not side:
                continue
            if match.get("type") == "doubles" and not team_members.get(side):
                conflicts.append({
                    "type": "unknown_team_members", "matchNumbers": [match.get("match_number")],
                    "detail": f"Match {match.get('match_number')} has a doubles team with no player membership on record.",
                })
                continue
            people.update(str(person) for person in team_members.get(side, [side]) if person)
        scheduled.append((match, start, start + timedelta(minutes=duration_minutes), people))

    for index, (left, left_start, left_end, left_people) in enumerate(scheduled):
        for right, right_start, right_end, right_people in scheduled[index + 1:]:
            numbers = [left.get("match_number"), right.get("match_number")]
            same_board = left.get("board_number") == right.get("board_number")
            overlap = left_start < right_end and right_start < left_end
            if same_board and overlap:
                conflicts.append({"type": "board_double_booked", "matchNumbers": numbers,
                                  "detail": f"Matches {numbers} overlap on board {left.get('board_number')}."})
            common = left_people & right_people
            if common and (left_start < right_end + timedelta(minutes=rest_minutes)
                           and right_start < left_end + timedelta(minutes=rest_minutes)):
                conflicts.append({"type": "participant_double_booked", "matchNumbers": numbers,
                                  "participantId": sorted(common)[0],
                                  "detail": f"A participant in matches {numbers} lacks the required rest time."})

        next_id = left.get("next_match_id")
        if next_id and str(next_id) in by_id:
            next_match = by_id[str(next_id)]
            next_start = _start(next_match)
            if next_start and next_start < left_end + timedelta(minutes=rest_minutes):
                conflicts.append({"type": "knockout_order",
                                  "matchNumbers": [left.get("match_number"), next_match.get("match_number")],
                                  "detail": "A knockout match starts before its feeder match and rest period finish."})
    return conflicts
