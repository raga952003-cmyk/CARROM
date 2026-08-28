import math
from typing import List, Dict, Any, Optional

def create_empty_boards(max_boards: int = 3, number_of_sets: int = 1) -> List[Dict[str, Any]]:
    """
    The empty boards a match starts with.

    Board numbers restart in each set, so three sets of eight is boards 1-8
    three times over rather than 1-24 — which is what the scorer sees on the
    sheet and what the set totals are grouped by.
    """
    boards = []
    for set_number in range(1, max(1, number_of_sets) + 1):
        for i in range(1, max_boards + 1):
            boards.append({
                "setNumber": set_number,
                "boardNumber": i,
                # Only the very first board of the match is live to begin with.
                "status": "in_progress" if (set_number == 1 and i == 1) else "pending",
                "player1Score": 0,
                "player2Score": 0,
                "queenClaimedBy": "none",
                "queenCovered": False,
                "foulsPlayer1": 0,
                "foulsPlayer2": 0,
                "whiteCoinsPocketed": 0,
                "blackCoinsPocketed": 0
            })
    return boards

def generate_round_robin_fixtures(
    tournament_id: str,
    participants: List[Dict[str, Any]],
    max_boards: int = 3,
    number_of_sets: int = 1,
    id_prefix: str = "rr",
) -> List[Dict[str, Any]]:
    n = len(participants)
    if n < 2:
        return []

    pool = list(participants)
    has_bye = (n % 2 != 0)
    if has_bye:
        pool.append({"id": "__BYE__", "name": "BYE"})

    num_participants = len(pool)
    num_rounds = num_participants - 1
    matches_per_round = num_participants // 2
    matches = []
    match_counter = 1

    # Circle / Polygon algorithm for round robin
    for round_idx in range(num_rounds):
        for match_idx in range(matches_per_round):
            home_idx = (round_idx + match_idx) % (num_participants - 1)
            away_idx = (num_participants - 1 - match_idx + round_idx) % (num_participants - 1)
            
            if match_idx == 0:
                away_idx = num_participants - 1

            p1 = pool[home_idx]
            p2 = pool[away_idx]

            # Skip match if one participant is BYE
            if p1.get("id") == "__BYE__" or p2.get("id") == "__BYE__":
                continue

            is_doubles = "player1_id" in p1 or "player1" in p1

            matches.append({
                "id": f"m_{tournament_id}_{id_prefix}_{round_idx + 1}_{match_counter}",
                "tournamentId": tournament_id,
                "matchNumber": match_counter,
                "roundName": f"League Round {round_idx + 1}",
                "roundIndex": round_idx + 1,
                "stage": "league",
                "type": "doubles" if is_doubles else "singles",
                "player1Id": p1["id"],
                "player2Id": p2["id"],
                "player1Name": p1["name"],
                "player2Name": p2["name"],
                "boardNumber": 1,
                "scheduledDate": "",
                "scheduledTime": "",
                "status": "scheduled",
                "timerElapsedSeconds": 0,
                "isTimerRunning": False,
                "boards": create_empty_boards(max_boards, number_of_sets),
                "numberOfSets": number_of_sets,
                "maxBoards": max_boards,
                "resultConfirmed": False,
                "player1BoardWins": 0,
                "player2BoardWins": 0,
                "player1TotalPoints": 0,
                "player2TotalPoints": 0,
                "auditHistory": []
            })
            match_counter += 1

    return matches

QUALIFIER_PREFIX = "qualifier_"


def _seed_order(size: int) -> List[int]:
    """
    Standard single-elimination seed positions, e.g. size 8 -> [1,8,4,5,2,7,3,6].

    Pairing adjacent entries gives 1v8, 4v5, 2v7, 3v6, which keeps the top seeds
    apart until the later rounds. The previous implementation paired entrants in
    registration order, so the two strongest could meet in round one.
    """
    order = [1]
    while len(order) < size:
        n = len(order) * 2
        expanded = []
        for seed in order:
            expanded.append(seed)
            expanded.append(n + 1 - seed)
        order = expanded
    return order


def _seeded_participants(participants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Explicit seed first, then rating (strongest first), then a stable name."""
    return sorted(
        participants,
        key=lambda p: (
            p.get("seed") if p.get("seed") is not None else 10 ** 6,
            -(p.get("rating") or 0),
            str(p.get("name") or ""),
        ),
    )


def _round_title(round_index: int, total_rounds: int) -> str:
    rounds_from_final = total_rounds - round_index
    if rounds_from_final == 0:
        return "Final"
    if rounds_from_final == 1:
        return "Semi Final"
    if rounds_from_final == 2:
        return "Quarter Final"
    if rounds_from_final == 3:
        return "Round of 16"
    return f"Round {round_index}"


def generate_knockout_bracket(
    tournament_id: str,
    participants: List[Dict[str, Any]],
    max_boards: int = 3,
    number_of_sets: int = 1,
    id_prefix: str = "ko",
) -> List[Dict[str, Any]]:
    """
    Single-elimination bracket with seeding and byes.

    When the entrant count is not a power of two the top seeds receive byes:
    their first-round match is not created at all and they are placed directly
    into the second round. Previously the surplus slots became matches against
    "Winner TBD" that could never be played, which left later rounds unfillable
    and the tournament unfinishable.
    """
    count = len(participants)
    if count < 2:
        return []

    ordered = _seeded_participants(participants)

    bracket_size = 2
    while bracket_size < count:
        bracket_size *= 2
    total_rounds = int(math.log2(bracket_size))

    # slots[i] is the entrant in bracket position i, or None for a bye.
    slots: List[Optional[Dict[str, Any]]] = []
    for seed in _seed_order(bracket_size):
        slots.append(ordered[seed - 1] if seed <= count else None)

    is_doubles = bool(ordered) and ("player1_id" in ordered[0] or "player1" in ordered[0])

    def blank_match(r: int, m: int) -> Dict[str, Any]:
        return {
            "id": f"m_{tournament_id}_{id_prefix}_r{r}_m{m + 1}",
            "tournamentId": tournament_id,
            "matchNumber": 0,  # assigned once byes are known
            "roundName": _round_title(r, total_rounds),
            "roundIndex": r,
            "stage": "knockout",
            "type": "doubles" if is_doubles else "singles",
            "player1Id": None,
            "player2Id": None,
            "player1Name": "Winner TBD",
            "player2Name": "Winner TBD",
            "boardNumber": 1,
            "scheduledDate": "",
            "scheduledTime": "",
            "status": "scheduled",
            "timerElapsedSeconds": 0,
            "isTimerRunning": False,
            "boards": create_empty_boards(max_boards, number_of_sets),
                "numberOfSets": number_of_sets,
            "maxBoards": max_boards,
            "resultConfirmed": False,
            "player1BoardWins": 0,
            "player2BoardWins": 0,
            "player1TotalPoints": 0,
            "player2TotalPoints": 0,
            "bracketPosition": {"round": r, "matchIndex": m},
            "auditHistory": [],
        }

    by_position: Dict[str, Dict[str, Any]] = {}
    for r in range(1, total_rounds + 1):
        for m in range(2 ** (total_rounds - r)):
            by_position[f"{r}_{m}"] = blank_match(r, m)

    def place(match: Dict[str, Any], slot: str, entrant: Dict[str, Any]) -> None:
        match[f"{slot}Id"] = entrant["id"]
        match[f"{slot}Name"] = entrant["name"]

    # ---- round 1: real pairings, or a bye straight into round 2 -------------
    skipped: set = set()
    for m in range(bracket_size // 2):
        p1, p2 = slots[m * 2], slots[m * 2 + 1]
        match = by_position[f"1_{m}"]

        if p1 and p2:
            place(match, "player1", p1)
            place(match, "player2", p2)
            continue

        # Exactly one entrant (a bye) or none. bracket_size is the *smallest*
        # power of two >= count, so count > bracket_size / 2 and a round-1 pair
        # can never be empty on both sides.
        advancing = p1 or p2
        skipped.add(f"1_{m}")
        if advancing and total_rounds >= 2:
            parent = by_position[f"2_{m // 2}"]
            place(parent, "player1" if m % 2 == 0 else "player2", advancing)
        elif advancing:
            # A two-entrant bracket where one side is a bye: nothing to play.
            place(match, "player1", advancing)
            skipped.discard(f"1_{m}")

    # ---- link each match to the one it feeds ------------------------------
    for r in range(1, total_rounds):
        for m in range(2 ** (total_rounds - r)):
            key = f"{r}_{m}"
            if key in skipped:
                continue
            child = by_position[key]
            parent = by_position[f"{r + 1}_{m // 2}"]
            child["nextMatchId"] = parent["id"]
            child["nextMatchSlot"] = "player1" if m % 2 == 0 else "player2"

    matches = [
        by_position[f"{r}_{m}"]
        for r in range(1, total_rounds + 1)
        for m in range(2 ** (total_rounds - r))
        if f"{r}_{m}" not in skipped
    ]
    for i, match in enumerate(matches, start=1):
        match["matchNumber"] = i

    return matches


def generate_league_knockout_fixtures(
    tournament_id: str,
    participants: List[Dict[str, Any]],
    max_boards: int = 3,
    number_of_sets: int = 1,
    id_prefix: str = "lk",
) -> List[Dict[str, Any]]:
    """
    Round-robin league followed by a knockout between the league's top finishers.

    The knockout slots start empty and are labelled "League Rank #n". They are
    filled by promoting the standings once the league is complete -- see
    services/qualification.py. They previously carried fabricated ids like
    "qualifier_1", which the database rejected outright because the column is a
    UUID, so this format could never generate fixtures at all.
    """
    league_matches = generate_round_robin_fixtures(
        tournament_id, participants, max_boards,
        number_of_sets=number_of_sets, id_prefix=f"{id_prefix}rr"
    )

    qualifier_count = min(4, max(2, len(participants) // 2))
    # Round down to a power of two so the bracket has no byes of its own.
    bracket_slots = 2
    while bracket_slots * 2 <= qualifier_count:
        bracket_slots *= 2

    placeholders = [
        {"id": f"{QUALIFIER_PREFIX}{i}", "name": f"League Rank #{i}", "seed": i}
        for i in range(1, bracket_slots + 1)
    ]

    knockout_matches = generate_knockout_bracket(
        tournament_id, placeholders, max_boards,
        number_of_sets=number_of_sets, id_prefix=f"{id_prefix}ko"
    )

    # Placeholders are not real participants: keep the label, drop the id so the
    # database stores NULL until a qualifier is promoted into the slot.
    for match in knockout_matches:
        for slot in ("player1", "player2"):
            if str(match.get(f"{slot}Id") or "").startswith(QUALIFIER_PREFIX):
                match[f"{slot}Id"] = None

    counter = 1
    for m in league_matches:
        m["matchNumber"] = counter
        counter += 1
    for m in knockout_matches:
        m["matchNumber"] = counter
        counter += 1

    return league_matches + knockout_matches


# ===========================================================================
# Group stage (spec 68): balanced allocation + round robin within each group
# ===========================================================================

def suggest_group_count(participant_count: int, target_size: int = 4) -> int:
    """
    How many groups to draw for this field.

    Aims for `target_size` per group while keeping every group at 3 or more,
    since a group of two is just a single match.
    """
    if participant_count < 6:
        return 1
    groups = max(1, round(participant_count / float(target_size)))
    while groups > 1 and participant_count / groups < 3:
        groups -= 1
    return int(groups)


def group_label(index: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'."""
    label = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        label = chr(65 + rem) + label
    return label


def allocate_groups(
    participants: List[Dict[str, Any]],
    group_count: int,
) -> List[List[Dict[str, Any]]]:
    """
    Balanced (serpentine) group allocation.

    Entrants are ranked, then dealt across the groups in a snake order --
    A,B,C,C,B,A,A,B,C... -- so each group receives a comparable spread of
    strength instead of all the top seeds landing together. Group sizes differ
    by at most one.
    """
    if group_count < 1:
        group_count = 1

    ranked = _seeded_participants(participants)
    groups: List[List[Dict[str, Any]]] = [[] for _ in range(group_count)]

    for position, participant in enumerate(ranked):
        row, offset = divmod(position, group_count)
        # Reverse direction on alternate passes: that is what makes it snake.
        index = offset if row % 2 == 0 else group_count - 1 - offset
        groups[index].append(participant)

    return [g for g in groups if g]


def generate_group_stage_fixtures(
    tournament_id: str,
    participants: List[Dict[str, Any]],
    max_boards: int = 3,
    number_of_sets: int = 1,
    group_count: Optional[int] = None,
    id_prefix: str = "gs",
) -> List[Dict[str, Any]]:
    """
    Round robin inside each balanced group.

    Every match carries its group in `bracketPosition.group` and in the round
    name, so standings and the UI can separate the groups without a schema
    change.
    """
    if len(participants) < 2:
        return []

    count = group_count or suggest_group_count(len(participants))
    groups = allocate_groups(participants, count)

    matches: List[Dict[str, Any]] = []
    for gi, members in enumerate(groups):
        if len(members) < 2:
            continue
        label = group_label(gi)
        drawn = generate_round_robin_fixtures(
            tournament_id, members, max_boards,
            number_of_sets=number_of_sets, id_prefix=f"{id_prefix}{label}"
        )
        for m in drawn:
            m["roundName"] = f"Group {label} · {m['roundName'].replace('League ', '')}"
            m["bracketPosition"] = {"group": label, "groupIndex": gi,
                                    "round": m["roundIndex"], "matchIndex": 0}
            m["groupName"] = label
        matches.extend(drawn)

    for i, m in enumerate(matches, start=1):
        m["matchNumber"] = i
    return matches


def generate_group_knockout_fixtures(
    tournament_id: str,
    participants: List[Dict[str, Any]],
    max_boards: int = 3,
    number_of_sets: int = 1,
    group_count: Optional[int] = None,
    qualifiers_per_group: int = 2,
    id_prefix: str = "gk",
) -> List[Dict[str, Any]]:
    """
    Balanced groups, then a knockout between the qualifiers from each group.

    Knockout slots start empty and are labelled "Group A #1", "Group B #2" and
    so on. They are filled from the group tables once the group stage finishes
    (services/qualification.py), which keeps the draw deterministic.
    """
    group_matches = generate_group_stage_fixtures(
        tournament_id, participants, max_boards,
        # By keyword: a positional argument here silently became the set count
        # when that parameter was added, and every group draw came out wrong.
        number_of_sets=number_of_sets, group_count=group_count,
        id_prefix=f"{id_prefix}g",
    )
    if not group_matches:
        return []

    labels = sorted({m["groupName"] for m in group_matches})

    # Standard cross-group pairing: winners meet runners-up from another group.
    placeholders: List[Dict[str, Any]] = []
    for rank in range(1, qualifiers_per_group + 1):
        ordered = labels if rank % 2 == 1 else list(reversed(labels))
        for label in ordered:
            placeholders.append({
                "id": f"{QUALIFIER_PREFIX}{label}_{rank}",
                "name": f"Group {label} #{rank}",
                "seed": (rank - 1) * len(labels) + labels.index(label) + 1,
            })

    # Every qualifier enters the bracket. Trimming to a power of two dropped
    # them by list position: with 12 groups, all 12 winners went through but
    # only four runners-up did, and which four depended on the order the labels
    # happened to be built in rather than on merit. The bracket generator
    # already handles a non-power-of-two field by giving byes to the top seeds,
    # which is both fair and the normal way to run this.
    knockout = generate_knockout_bracket(
        tournament_id, placeholders, max_boards,
        number_of_sets=number_of_sets, id_prefix=f"{id_prefix}ko"
    )
    for m in knockout:
        for side in ("player1", "player2"):
            if str(m.get(f"{side}Id") or "").startswith(QUALIFIER_PREFIX):
                m[f"{side}Id"] = None

    counter = 1
    for m in group_matches + knockout:
        m["matchNumber"] = counter
        counter += 1
    return group_matches + knockout
