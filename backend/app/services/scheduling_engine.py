from datetime import datetime, timedelta
from typing import List, Dict, Any

def format_time_slot(base_date_str: str, minutes_from_start: int):
    try:
        # Try standard YYYY-MM-DD
        base_date = datetime.strptime(base_date_str, "%Y-%m-%d")
    except Exception:
        # Fallback
        base_date = datetime.now()
    
    # Start at 09:00 AM
    start_time = base_date.replace(hour=9, minute=0, second=0, microsecond=0)
    scheduled_dt = start_time + timedelta(minutes=minutes_from_start)
    
    formatted_date = scheduled_dt.strftime("%Y-%m-%d")
    # Format time as "09:00 AM" (12-hour format with AM/PM)
    formatted_time = scheduled_dt.strftime("%I:%M %p").lstrip("0")
    
    return formatted_date, formatted_time

def generate_conflict_free_schedule(
    matches: List[Dict[str, Any]],
    number_of_boards: int,
    start_date: str,
    match_duration_minutes: int = 30,
    rest_time_minutes: int = 10
) -> List[Dict[str, Any]]:
    if not matches:
        return []

    board_count = max(1, number_of_boards)
    # Track participant availability: participant_id -> next available minute timestamp
    participant_next_available: Dict[str, int] = {}
    # Track board availability: board_index (1..N) -> next available minute timestamp
    board_next_available = [0] * (board_count + 1)

    updated_matches = [dict(m) for m in matches]

    # Sort matches: league first, knockout rounds after
    def get_sort_key(m):
        stage_weight = 0 if m.get("stage") == "league" else 1
        round_idx = m.get("roundIndex", 0)
        return (stage_weight, round_idx)

    sorted_matches = sorted(updated_matches, key=get_sort_key)
    current_slot_offset = 0

    for match in sorted_matches:
        # For a doubles match player1Id is a *team* id, so scheduling on it
        # alone would happily put the same person on two boards at once when
        # they are entered in both categories. participantIds carries the
        # underlying people when the caller can supply them.
        people = [pid for pid in (match.get("participantIds") or []) if pid and pid != "TBD"]
        if not people:
            people = [pid for pid in (match.get("player1Id"), match.get("player2Id"))
                      if pid and pid != "TBD"]

        match_earliest = max(
            [current_slot_offset] + [participant_next_available.get(pid, 0) for pid in people]
        )

        # Find a board that is free at or before match_earliest, or find the board that frees up earliest
        chosen_board = 1
        min_board_free_time = float("inf")

        for b in range(1, board_count + 1):
            if board_next_available[b] <= match_earliest:
                chosen_board = b
                min_board_free_time = board_next_available[b]
                break
            if board_next_available[b] < min_board_free_time:
                min_board_free_time = board_next_available[b]
                chosen_board = b

        actual_start_time = max(match_earliest, min_board_free_time)
        finish_time = actual_start_time + match_duration_minutes
        next_available_time_for_players = finish_time + rest_time_minutes
        next_available_time_for_board = finish_time + 5  # 5 min buffer to prep board

        match["boardNumber"] = chosen_board
        date, time = format_time_slot(start_date, actual_start_time)
        match["scheduledDate"] = date
        match["scheduledTime"] = time

        # Update trackers
        board_next_available[chosen_board] = next_available_time_for_board
        for pid in people:
            participant_next_available[pid] = next_available_time_for_players

    return sorted_matches
