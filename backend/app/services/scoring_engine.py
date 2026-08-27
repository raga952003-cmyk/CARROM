from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

def _field(row: Dict[str, Any], camel: str, snake: str, default: Any = None) -> Any:
    """Rows reach these helpers either as camelCase payloads or raw snake_case
    database rows, so look up both spellings."""
    if camel in row and row[camel] is not None:
        return row[camel]
    if snake in row and row[snake] is not None:
        return row[snake]
    return default


def calculate_board_winner(board: Dict[str, Any]) -> str:
    """Returns 'player1', 'player2', 'draw', or 'in_progress'"""
    if board.get("status") != "completed":
        return "in_progress"

    p1 = _field(board, "player1Score", "player1_score", 0)
    p2 = _field(board, "player2Score", "player2_score", 0)
    
    if p1 > p2:
        return "player1"
    elif p2 > p1:
        return "player2"
    return "draw"

def recalculate_match_scores(match: Dict[str, Any], boards: List[Dict[str, Any]]) -> Dict[str, Any]:
    p1_board_wins = 0
    p2_board_wins = 0
    p1_total_points = 0
    p2_total_points = 0

    completed_count = 0
    for b in boards:
        status = b.get("status")
        p1_score = _field(b, "player1Score", "player1_score", 0)
        p2_score = _field(b, "player2Score", "player2_score", 0)
        
        if status == "completed":
            completed_count += 1
            p1_total_points += p1_score
            p2_total_points += p2_score
            if p1_score > p2_score:
                p1_board_wins += 1
            elif p2_score > p1_score:
                p2_board_wins += 1

    updated_match = dict(match)
    updated_match["player1BoardWins"] = p1_board_wins
    updated_match["player2BoardWins"] = p2_board_wins
    updated_match["player1TotalPoints"] = p1_total_points
    updated_match["player2TotalPoints"] = p2_total_points

    # Determine winner based on board wins
    max_boards = _field(match, "maxBoards", "max_boards", 3)
    target_wins = (max_boards // 2) + 1

    winner_id = None
    winner_name = None
    
    # Best of N boards check
    if p1_board_wins >= target_wins:
        winner_id = _field(match, "player1Id", "player1_id")
        winner_name = _field(match, "player1Name", "player1_name")
        updated_match["status"] = "completed"
        updated_match["matchCompletedAt"] = datetime.utcnow().isoformat()
    elif p2_board_wins >= target_wins:
        winner_id = _field(match, "player2Id", "player2_id")
        winner_name = _field(match, "player2Name", "player2_name")
        updated_match["status"] = "completed"
        updated_match["matchCompletedAt"] = datetime.utcnow().isoformat()
    elif boards and completed_count == len(boards):
        # All boards completed but no one reached target wins (possible draws or flat format)
        # `boards` is guarded: an empty list would otherwise satisfy 0 == 0 and
        # instantly complete a match that has not been played.
        if p1_board_wins > p2_board_wins:
            winner_id = _field(match, "player1Id", "player1_id")
            winner_name = _field(match, "player1Name", "player1_name")
        elif p2_board_wins > p1_board_wins:
            winner_id = _field(match, "player2Id", "player2_id")
            winner_name = _field(match, "player2Name", "player2_name")
        
        updated_match["status"] = "completed"
        updated_match["matchCompletedAt"] = datetime.utcnow().isoformat()

    updated_match["winnerId"] = winner_id
    updated_match["winnerName"] = winner_name

    return updated_match

def calculate_points_table(
    matches: List[Dict[str, Any]],
    participants: List[Dict[str, Any]],
    rules: Dict[str, Any]
) -> List[Dict[str, Any]]:
    standings_map = {}

    pts_win = rules.get("pointsForWin", 2) if "pointsForWin" in rules else rules.get("points_for_win", 2)
    pts_draw = rules.get("pointsForDraw", 1) if "pointsForDraw" in rules else rules.get("points_for_draw", 1)
    pts_loss = rules.get("pointsForLoss", 0) if "pointsForLoss" in rules else rules.get("points_for_loss", 0)

    for p in participants:
        p_id = p["id"]
        is_doubles = "player1_id" in p or "player1" in p
        standings_map[p_id] = {
            "rank": 1,
            "participantId": p_id,
            "participantName": p["name"],
            "played": 0,
            "won": 0,
            "lost": 0,
            "drawn": 0,
            "boardWins": 0,
            "boardLosses": 0,
            "boardDiff": 0,
            "scoreFor": 0,
            "scoreAgainst": 0,
            "scoreDiff": 0,
            "points": 0,
            "form": [],
            "participantType": "doubles" if is_doubles else "singles",
            "isQualified": False
        }

    # Filter completed & confirmed league matches
    league_matches = [
        m for m in matches 
        if (m.get("resultConfirmed") or m.get("result_confirmed")) and m.get("stage") == "league"
    ]

    for m in league_matches:
        p1_id = m.get("player1Id") or m.get("player1_id")
        p2_id = m.get("player2Id") or m.get("player2_id")

        s1 = standings_map.get(p1_id)
        s2 = standings_map.get(p2_id)

        if not s1 or not s2:
            continue

        s1["played"] += 1
        s2["played"] += 1

        b_wins1 = m.get("player1BoardWins", 0) if "player1BoardWins" in m else m.get("player1_board_wins", 0)
        b_wins2 = m.get("player2BoardWins", 0) if "player2BoardWins" in m else m.get("player2_board_wins", 0)
        pts1 = m.get("player1TotalPoints", 0) if "player1TotalPoints" in m else m.get("player1_total_points", 0)
        pts2 = m.get("player2TotalPoints", 0) if "player2TotalPoints" in m else m.get("player2_total_points", 0)
        winner_id = m.get("winnerId") or m.get("winner_id")

        s1["boardWins"] += b_wins1
        s1["boardLosses"] += b_wins2
        s2["boardWins"] += b_wins2
        s2["boardLosses"] += b_wins1

        s1["scoreFor"] += pts1
        s1["scoreAgainst"] += pts2
        s2["scoreFor"] += pts2
        s2["scoreAgainst"] += pts1

        if winner_id == p1_id:
            s1["won"] += 1
            s1["points"] += pts_win
            s1["form"].append("W")

            s2["lost"] += 1
            s2["points"] += pts_loss
            s2["form"].append("L")
        elif winner_id == p2_id:
            s2["won"] += 1
            s2["points"] += pts_win
            s2["form"].append("W")

            s1["lost"] += 1
            s1["points"] += pts_loss
            s1["form"].append("L")
        else:
            # Draw
            s1["drawn"] += 1
            s1["points"] += pts_draw
            s1["form"].append("D")

            s2["drawn"] += 1
            s2["points"] += pts_draw
            s2["form"].append("D")

    # Convert map to list and calculate differentials
    rows = list(standings_map.values())
    for r in rows:
        r["boardDiff"] = r["boardWins"] - r["boardLosses"]
        r["scoreDiff"] = r["scoreFor"] - r["scoreAgainst"]
        r["form"] = r["form"][-5:]  # Keep last 5

    # Sort tiebreakers: 1. Points, 2. boardDiff, 3. scoreDiff, 4. scoreFor, 5. name
    def sort_key(r):
        return (
            -r["points"],
            -r["boardDiff"],
            -r["scoreDiff"],
            -r["scoreFor"],
            r["participantName"]
        )

    rows.sort(key=sort_key)

    # Assign ranks and qualification status (Top 4 qualify)
    for idx, r in enumerate(rows):
        r["rank"] = idx + 1
        r["isQualified"] = (idx < 4)

    return rows


def queen_award(
    queen_claimed_by: Optional[str],
    queen_covered: Optional[bool],
    rules: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int, Optional[str]]:
    """
    Points to add for the queen, as (player1_bonus, player2_bonus, note).

    Scorers enter the coin count only; the queen is added here from the
    tournament's configured value. Previously `queenPoints` existed in the
    rules and in the setup screen but no scoring code read it, so choosing 1
    or 3 changed nothing.

    A queen only counts if it was covered -- pocketing it and failing to follow
    with your own coin returns it to the board -- so a claimed but uncovered
    queen scores nothing.
    """
    rules = rules or {}
    points = rules.get("queenPoints", rules.get("queen_points", 3))
    try:
        points = int(points)
    except (TypeError, ValueError):
        points = 3

    if queen_claimed_by not in ("player1", "player2"):
        return 0, 0, None

    if not queen_covered:
        return 0, 0, "queen claimed but not covered, so it scores nothing"

    if queen_claimed_by == "player1":
        return points, 0, f"+{points} to player 1 for the queen"
    return 0, points, f"+{points} to player 2 for the queen"


def apply_queen_points(
    p1_score: int,
    p2_score: int,
    queen_claimed_by: Optional[str],
    queen_covered: Optional[bool],
    rules: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int, Optional[str]]:
    """Coin counts in, effective board scores out."""
    bonus1, bonus2, note = queen_award(queen_claimed_by, queen_covered, rules)
    return p1_score + bonus1, p2_score + bonus2, note
