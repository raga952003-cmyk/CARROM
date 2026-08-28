from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

SIDES = ("player1", "player2")


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

def scoring_mode(rules: Optional[Dict[str, Any]]) -> str:
    """
    'classic'         — each side keeps the coins it pocketed (the original engine).
    'remaining_coins' — only the board winner scores, and scores the coins the
                        loser still had on the board.

    Tournaments created before the second model existed have no setting and stay
    on 'classic', so their confirmed results keep the totals they were decided on.
    """
    mode = _rule(rules or {}, "scoringMode", "scoring_mode", "classic")
    return mode if mode in ("classic", "remaining_coins") else "classic"


def recalculate_match_scores(
    match: Dict[str, Any],
    boards: List[Dict[str, Any]],
    rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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

            # Who won the board is what the umpire recorded, when they recorded
            # it. Comparing the two scores gets it backwards whenever the losing
            # side covered the queen: winning a board worth 2 against a queen
            # worth 3 is still winning the board.
            declared = _field(b, "boardWinner", "board_winner")
            if declared in SIDES:
                if declared == "player1":
                    p1_board_wins += 1
                else:
                    p2_board_wins += 1
            elif p1_score > p2_score:
                p1_board_wins += 1
            elif p2_score > p1_score:
                p2_board_wins += 1

    updated_match = dict(match)
    updated_match["player1BoardWins"] = p1_board_wins
    updated_match["player2BoardWins"] = p2_board_wins
    updated_match["player1TotalPoints"] = p1_total_points
    updated_match["player2TotalPoints"] = p2_total_points

    max_boards = _field(match, "maxBoards", "max_boards", 3)
    target_wins = (max_boards // 2) + 1

    winner_id = None
    winner_name = None

    def side(n: int):
        return (_field(match, f"player{n}Id", f"player{n}_id"),
                _field(match, f"player{n}Name", f"player{n}_name"))

    if scoring_mode(rules) == "remaining_coins":
        # Every board is played. A side that has already won most boards can
        # still lose on points, so the match is not called early.
        updated_match["tieBreakRequired"] = False
        if boards and completed_count == len(boards):
            if p1_total_points > p2_total_points:
                winner_id, winner_name = side(1)
            elif p2_total_points > p1_total_points:
                winner_id, winner_name = side(2)
            else:
                rule = _rule(rules or {}, "tieBreak", "tie_break", "additional_board")
                if rule == "most_board_wins" and p1_board_wins != p2_board_wins:
                    winner_id, winner_name = side(1) if p1_board_wins > p2_board_wins else side(2)
                else:
                    # An extra board, sudden death or an organiser ruling all
                    # need a human, so the match stays open and says so.
                    updated_match["tieBreakRequired"] = True
                    updated_match["tieBreakRule"] = rule
            if not updated_match["tieBreakRequired"]:
                updated_match["status"] = "completed"
                updated_match["matchCompletedAt"] = datetime.utcnow().isoformat()
        updated_match["winnerId"] = winner_id
        updated_match["winnerName"] = winner_name
        return updated_match

    # Classic: best of N boards, decided as soon as one side is out of reach.
    if p1_board_wins >= target_wins:
        winner_id, winner_name = side(1)
        updated_match["status"] = "completed"
        updated_match["matchCompletedAt"] = datetime.utcnow().isoformat()
    elif p2_board_wins >= target_wins:
        winner_id, winner_name = side(2)
        updated_match["status"] = "completed"
        updated_match["matchCompletedAt"] = datetime.utcnow().isoformat()
    elif boards and completed_count == len(boards):
        # Every board played without either side reaching the target: decide on
        # board wins, or leave it drawn. `boards` is guarded because an empty
        # list would otherwise satisfy 0 == 0 and complete an unplayed match.
        if p1_board_wins > p2_board_wins:
            winner_id, winner_name = side(1)
        elif p2_board_wins > p1_board_wins:
            winner_id, winner_name = side(2)
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

    # Points, then matches won, then board difference, then score difference.
    # Matches won sits second because two players level on points have not
    # necessarily won the same number of matches — a draw and a win pay
    # differently — and beating people is the more direct claim.
    def sort_key(r):
        return (
            -r["points"],
            -r["won"],
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


# --------------------------------------------------------------------------
# Remaining-coins board scoring
#
# The classic engine above lets each player keep the coins they pocketed. Real
# tournament carrom scores differently: only the side that finishes the board
# scores, and what they score is the number of coins their OPPONENT still has
# on the board, plus the queen if it was covered, less any penalties.
#
# Both models are kept because switching formula retroactively would rewrite
# the totals of every match already confirmed under the old one.
# --------------------------------------------------------------------------

def _opponent(side: Optional[str]) -> Optional[str]:
    if side == "player1":
        return "player2"
    if side == "player2":
        return "player1"
    return None


def _rule(rules: Dict[str, Any], camel: str, snake: str, default: Any) -> Any:
    if camel in rules and rules[camel] is not None:
        return rules[camel]
    if snake in rules and rules[snake] is not None:
        return rules[snake]
    return default


def board_result(
    *,
    winner: Optional[str] = "none",
    p1_coins_pocketed: Optional[int] = None,
    p2_coins_pocketed: Optional[int] = None,
    coins_remaining_with: Optional[str] = None,
    coins_remaining: Optional[int] = None,
    queen_pocketed_by: Optional[str] = "none",
    queen_covered_by: Optional[str] = "none",
    p1_penalty: int = 0,
    p2_penalty: int = 0,
    rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Score one board from what the umpire observed.

    Every input is recorded exactly as given. Nothing is inferred from anything
    else: naming a winner does not decide who took the queen, and taking the
    queen does not decide who won. Where two inputs disagree the disagreement
    is reported in `warnings` rather than silently resolved, because only the
    umpire at the board can say which one is right.
    """
    rules = rules or {}
    coins_per_side = int(_rule(rules, "coinsPerSide", "coins_per_side", 9))
    queen_points = int(_rule(rules, "queenPoints", "queen_points", 3))
    # What one coin is worth. 1 in standard carrom, but the spec is explicit
    # that it belongs in the tournament rules rather than in the arithmetic.
    try:
        coin_value = int(_rule(rules, "coinValue", "coin_value", 1))
    except (TypeError, ValueError):
        coin_value = 1
    must_cover = bool(_rule(rules, "queenMustBeCovered", "queen_must_be_covered", True))
    # Who the queen pays: whoever covered it, or whoever sank it.
    award_to = _rule(rules, "queenAwardTo", "queen_award_to", "coverer")

    warnings: List[str] = []
    winner = winner if winner in SIDES else "none"
    loser = _opponent(winner)

    # ---- base points: the coins the loser still has on the board ----------
    #
    # Pocketed counts are optional. A scorer who counts what was potted gets a
    # cross-check against what they said was left; one who just reports the
    # coins on the board gets no spurious disagreement with a number they
    # never entered.
    pocketed = {
        "player1": None if p1_coins_pocketed is None else max(0, int(p1_coins_pocketed)),
        "player2": None if p2_coins_pocketed is None else max(0, int(p2_coins_pocketed)),
    }
    derived = {
        s: None if pocketed[s] is None else max(0, coins_per_side - pocketed[s])
        for s in SIDES
    }

    if winner == "none":
        base = 0
    elif coins_remaining_with in SIDES:
        # The umpire named who still has coins on the board.
        if coins_remaining is not None:
            stated = max(0, int(coins_remaining))
        else:
            stated = derived[coins_remaining_with] or 0

        if coins_remaining_with == winner:
            base = 0
            warnings.append(
                f"{winner} is recorded as both the board winner and the side with coins left; "
                "scored 0 base points"
            )
        else:
            base = stated
            expected = derived[coins_remaining_with]
            if coins_remaining is not None and expected is not None and stated != expected:
                warnings.append(
                    f"coins remaining ({stated}) does not match {coins_per_side} minus the "
                    f"{pocketed[coins_remaining_with]} pocketed ({expected})"
                )
    elif coins_remaining_with == "none":
        # "Nobody has coins left" means nobody has coins left. A count still
        # sitting in the payload from an earlier selection must not be scored.
        base = 0
    else:
        # Not stated — fall back to the arithmetic on the coin counts, if any.
        base = (derived[loser] or 0) if loser else 0

    # ---- queen ------------------------------------------------------------
    pocketed_by = queen_pocketed_by if queen_pocketed_by in SIDES else "none"
    covered_by = queen_covered_by if queen_covered_by in SIDES else "none"
    covered = covered_by in SIDES

    if pocketed_by == "none":
        queen_status = "not_pocketed"
    elif covered or not must_cover:
        queen_status = "covered"
    else:
        queen_status = "returned"

    if covered_by in SIDES and pocketed_by == "none":
        warnings.append("the queen is recorded as covered but nobody is recorded as pocketing it")

    queen_side = None
    queen_bonus = 0
    if queen_status == "covered" and pocketed_by in SIDES:
        queen_side = covered_by if (award_to == "coverer" and covered_by in SIDES) else pocketed_by
        queen_bonus = queen_points
        if covered_by in SIDES and covered_by != pocketed_by:
            warnings.append(
                f"{pocketed_by} pocketed the queen but {covered_by} covered it; "
                f"the {queen_points} points went to {queen_side}"
            )
    elif queen_status == "returned":
        warnings.append("the queen was pocketed but not covered, so it scores nothing and returns to the board")

    # ---- assemble ---------------------------------------------------------
    points = {"player1": 0, "player2": 0}
    if winner in SIDES:
        points[winner] += base * coin_value
    if queen_side in SIDES:
        points[queen_side] += queen_bonus

    penalty = {"player1": max(0, int(p1_penalty or 0)), "player2": max(0, int(p2_penalty or 0))}
    for s in SIDES:
        # A board cannot be worth less than nothing; penalties cannot push a
        # side into debt that would then be subtracted from their match total.
        points[s] = max(0, points[s] - penalty[s])

    return {
        "player1_score": points["player1"],
        "player2_score": points["player2"],
        "board_winner": winner,
        "base_points": base * coin_value,
        "queen_bonus": queen_bonus,
        "queen_awarded_to": queen_side or "none",
        "queen_status": queen_status,
        "p1_coins_remaining": derived["player1"],
        "p2_coins_remaining": derived["player2"],
        "penalties": {"player1": penalty["player1"], "player2": penalty["player2"]},
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# Sets
#
# The Carromite format puts a set between the match and its boards: three sets
# of eight boards is twenty-four boards, each set is won on the points scored
# within it, and the match is won on sets. A player can therefore score fewer
# points across the match and still win it, which is the whole reason the
# layer exists and something a flat list of boards cannot express.
# --------------------------------------------------------------------------


def set_layout(match: Dict[str, Any], rules: Optional[Dict[str, Any]] = None) -> Tuple[int, int]:
    """(number_of_sets, boards_per_set) for a match, falling back to one set."""
    rules = rules or {}
    sets = _field(match, "numberOfSets", "number_of_sets") \
        or _rule(rules, "numberOfSets", "number_of_sets", 1)
    per_set = _rule(rules, "boardsPerSet", "boards_per_set", None) \
        or _field(match, "maxBoards", "max_boards", 8)
    try:
        sets = max(1, int(sets))
    except (TypeError, ValueError):
        sets = 1
    try:
        per_set = max(1, int(per_set))
    except (TypeError, ValueError):
        per_set = 8
    return sets, per_set



def _board_winner_of(board: Dict[str, Any]) -> Optional[str]:
    """The side that won a board: what was recorded, or the higher score."""
    declared = _field(board, "boardWinner", "board_winner")
    if declared in SIDES:
        return declared
    p1 = _field(board, "player1Score", "player1_score", 0) or 0
    p2 = _field(board, "player2Score", "player2_score", 0) or 0
    if p1 > p2:
        return "player1"
    if p2 > p1:
        return "player2"
    return None


def summarise_sets(
    match: Dict[str, Any],
    boards: List[Dict[str, Any]],
    rules: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    One row per set: points each way, whether it is finished, and who took it.

    Boards written before sets existed carry no set_number and default to 1, so
    an older match reads as a single set and scores exactly as it always did.
    """
    total_sets, per_set = set_layout(match, rules)
    set_rule = _rule(rules or {}, "setWinnerRule", "set_winner_rule", "total_points")
    p1_id = _field(match, "player1Id", "player1_id")
    p2_id = _field(match, "player2Id", "player2_id")
    p1_name = _field(match, "player1Name", "player1_name")
    p2_name = _field(match, "player2Name", "player2_name")

    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for b in boards:
        n = _field(b, "setNumber", "set_number", 1) or 1
        grouped.setdefault(int(n), []).append(b)

    rows = []
    for set_number in range(1, total_sets + 1):
        members = grouped.get(set_number, [])
        p1 = p2 = 0
        done = 0
        for b in members:
            if b.get("status") != "completed":
                continue
            done += 1
            p1 += _field(b, "player1Score", "player1_score", 0) or 0
            p2 += _field(b, "player2Score", "player2_score", 0) or 0

        expected = len(members) or per_set
        complete = done > 0 and done >= expected

        winner_id = winner_name = None
        if complete:
            # Which side took the set is a tournament rule, not an assumption.
            # Winning most boards and scoring most points can disagree — three
            # narrow boards against one landslide — and different associations
            # settle that differently.
            if set_rule == "board_wins":
                w1 = sum(1 for b in members
                         if b.get("status") == "completed"
                         and _board_winner_of(b) == "player1")
                w2 = sum(1 for b in members
                         if b.get("status") == "completed"
                         and _board_winner_of(b) == "player2")
                lead1, lead2 = w1, w2
            else:
                lead1, lead2 = p1, p2

            if lead1 > lead2:
                winner_id, winner_name = p1_id, p1_name
            elif lead2 > lead1:
                winner_id, winner_name = p2_id, p2_name

        rows.append({
            "setNumber": set_number,
            "status": "completed" if complete else ("in_progress" if done else "pending"),
            "boardsCompleted": done,
            "boardsExpected": expected,
            "player1Points": p1,
            "player2Points": p2,
            "winnerId": winner_id,
            "winnerName": winner_name,
        })
    return rows


def apply_set_results(
    match: Dict[str, Any],
    boards: List[Dict[str, Any]],
    rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Decide a match on sets won rather than on total points.

    Layered on top of `recalculate_match_scores`, which still produces the
    board totals every existing screen reads; this only changes who is declared
    the winner, and only when the tournament is actually played in sets.
    """
    updated = recalculate_match_scores(match, boards, rules)
    total_sets, _ = set_layout(match, rules)
    set_rule = _rule(rules or {}, "setWinnerRule", "set_winner_rule", "total_points")

    if total_sets <= 1:
        # A best-of-1 is still a set, so the set rule decides it. Only worth
        # re-deciding when the rule is not the points default, or an existing
        # tournament would change behaviour under it.
        if set_rule == "total_points":
            return updated
        row = summarise_sets(match, boards, rules)[0]
        if row["status"] != "completed":
            return updated
        updated["winnerId"] = row["winnerId"]
        updated["winnerName"] = row["winnerName"]
        updated["status"] = "completed" if row["winnerId"] else updated.get("status", "live")
        if row["winnerId"]:
            updated["matchCompletedAt"] = datetime.utcnow().isoformat()
        return updated

    sets = summarise_sets(match, boards, rules)
    updated["sets"] = sets

    p1_id = _field(match, "player1Id", "player1_id")
    p2_id = _field(match, "player2Id", "player2_id")
    p1_sets = sum(1 for s in sets if s["winnerId"] == p1_id and p1_id)
    p2_sets = sum(1 for s in sets if s["winnerId"] == p2_id and p2_id)
    updated["player1SetsWon"] = p1_sets
    updated["player2SetsWon"] = p2_sets

    # Best of N: once a side has more sets than the rest can yield, the match
    # is over and the remaining sets are not played. This is not the same as
    # the board rule inside a set — every board of a set IS played out, because
    # a set is decided on its total, not on a running board lead.
    needed = (total_sets // 2) + 1
    if p1_sets >= needed or p2_sets >= needed:
        won_by_1 = p1_sets >= needed
        updated["winnerId"] = p1_id if won_by_1 else p2_id
        updated["winnerName"] = _field(
            match, "player1Name" if won_by_1 else "player2Name",
            "player1_name" if won_by_1 else "player2_name")
        updated["status"] = "completed"
        updated["matchCompletedAt"] = datetime.utcnow().isoformat()
        updated["tieBreakRequired"] = False
        updated["setsUnplayed"] = sum(1 for s in sets if s["status"] == "pending")
        return updated

    if all(s["status"] == "completed" for s in sets):
        if p1_sets > p2_sets:
            updated["winnerId"] = p1_id
            updated["winnerName"] = _field(match, "player1Name", "player1_name")
        elif p2_sets > p1_sets:
            updated["winnerId"] = p2_id
            updated["winnerName"] = _field(match, "player2Name", "player2_name")
        else:
            # Level on sets. Fall back to total points, then to the tie-break.
            p1_pts = updated["player1TotalPoints"]
            p2_pts = updated["player2TotalPoints"]
            if p1_pts > p2_pts:
                updated["winnerId"] = p1_id
                updated["winnerName"] = _field(match, "player1Name", "player1_name")
            elif p2_pts > p1_pts:
                updated["winnerId"] = p2_id
                updated["winnerName"] = _field(match, "player2Name", "player2_name")
            else:
                updated["winnerId"] = None
                updated["winnerName"] = None
                updated["tieBreakRequired"] = True
                updated["tieBreakRule"] = _rule(
                    rules or {}, "tieBreak", "tie_break", "additional_board")

        updated["status"] = "completed" if updated.get("winnerId") else updated.get("status", "live")
        if updated.get("winnerId"):
            updated["matchCompletedAt"] = datetime.utcnow().isoformat()
            updated["tieBreakRequired"] = False
    else:
        # Not every set is in yet, so nothing is decided.
        updated["winnerId"] = None
        updated["winnerName"] = None
        updated["status"] = match.get("status", "live")
        updated["matchCompletedAt"] = None
        updated["tieBreakRequired"] = False

    return updated
