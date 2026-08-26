"""
Participant sheet parsing (spec 67).

Handles both shapes an organiser actually sends:

  Singles   Name | Club | City | Rating | Seed | Email | Phone
  Doubles   Team Name | Player 1 Name | Player 2 Name | Club | City | ...

The previous parser picked the first column whose header merely *contained*
"name". On a doubles sheet that is "Team Name", so it imported the team as if it
were a person and silently discarded both real players. Column resolution here
is explicit and ordered, and doubles rows keep both players.
"""
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd


def _norm(value: Any) -> str:
    return str(value).strip().lower().replace("_", " ").replace(".", " ")


def _collapse(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum())


def pick_column(
    columns: List[str],
    candidates: List[str],
    exclude: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Resolve a column by trying exact matches before loose ones, so a specific
    header always beats a generic substring hit.
    """
    exclude = set(exclude or [])
    available = [c for c in columns if c not in exclude]

    collapsed = {c: _collapse(c) for c in available}

    # 1. exact match
    for cand in candidates:
        target = _collapse(cand)
        for c in available:
            if collapsed[c] == target:
                return c
    # 2. header starts with the candidate
    for cand in candidates:
        target = _collapse(cand)
        for c in available:
            if collapsed[c].startswith(target):
                return c
    # 3. candidate appears anywhere
    for cand in candidates:
        target = _collapse(cand)
        for c in available:
            if target in collapsed[c]:
                return c
    return None


TEAM_CANDIDATES = ["team name", "teamname", "team", "pair name", "pair", "doubles team"]
P1_CANDIDATES = ["player 1 name", "player1 name", "player 1", "player1", "p1 name", "p1",
                 "first player", "captain"]
P2_CANDIDATES = ["player 2 name", "player2 name", "player 2", "player2", "p2 name", "p2",
                 "partner name", "partner", "second player"]
SINGLES_NAME_CANDIDATES = ["name", "player name", "full name", "participant", "player"]
TYPE_CANDIDATES = ["type", "category", "event"]


def resolve_columns(columns: List[str]) -> Dict[str, Optional[str]]:
    """Work out which column means what, and whether this is a doubles sheet."""
    team_col = pick_column(columns, TEAM_CANDIDATES)
    p1_col = pick_column(columns, P1_CANDIDATES, exclude=[team_col] if team_col else [])
    p2_col = pick_column(columns, P2_CANDIDATES,
                         exclude=[c for c in (team_col, p1_col) if c])

    used = [c for c in (team_col, p1_col, p2_col) if c]
    # The singles name column must not be one already claimed above.
    name_col = pick_column(columns, SINGLES_NAME_CANDIDATES, exclude=used)

    # A second player column is what makes a sheet doubles.
    is_doubles = bool(p2_col)

    return {
        "team": team_col,
        "player1": p1_col or (name_col if is_doubles else None),
        "player2": p2_col,
        "name": name_col,
        "type": pick_column(columns, TYPE_CANDIDATES, exclude=used + ([name_col] if name_col else [])),
        "club": pick_column(columns, ["club", "academy", "association"], exclude=used),
        "city": pick_column(columns, ["city", "town", "district"], exclude=used),
        "rating": pick_column(columns, ["rating", "rank points", "points", "rate"], exclude=used),
        "seed": pick_column(columns, ["seed", "seeding"], exclude=used),
        "email": pick_column(columns, ["email", "e mail", "mail"], exclude=used),
        "phone": pick_column(columns, ["phone", "mobile", "contact"], exclude=used),
        "partner_email": pick_column(columns, ["player 2 email", "partner email", "p2 email"]),
        "partner_phone": pick_column(columns, ["player 2 phone", "partner phone", "p2 phone",
                                               "player 2 mobile", "partner mobile"]),
        "is_doubles_sheet": is_doubles,
    }


def _cell(row, col: Optional[str], default=None):
    if not col or col not in row.index:
        return default
    value = row[col]
    if pd.isna(value):
        return default
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return default
    return text


def _int_cell(row, col: Optional[str], default, errors: List[str], label: str):
    raw = _cell(row, col)
    if raw is None:
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        errors.append(label)
        return default


def parse_participants(df: pd.DataFrame) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """
    Returns (entries, errors, meta).

    Each entry is flat so the review table can render it directly:
        singles: {type, name, club, city, rating, seed, email, phone, selected}
        doubles: the same plus {teamName, partnerName, partnerEmail, partnerPhone}
    """
    df.columns = [_norm(c) for c in df.columns]
    columns = list(df.columns)
    cols = resolve_columns(columns)
    errors: List[str] = []

    if not cols["player1"] and not cols["name"]:
        raise ValueError(
            "Could not find a player name column. Expected a 'Name' column for singles, "
            "or 'Player 1 Name' and 'Player 2 Name' for doubles. "
            "Found: {}".format(", ".join(columns) or "no columns")
        )

    entries: List[Dict[str, Any]] = []

    for idx, row in df.iterrows():
        line = idx + 2  # +1 for the header row, +1 for 1-based numbering

        row_type = (_cell(row, cols["type"]) or "").lower()
        if row_type.startswith("d"):
            is_doubles = True
        elif row_type.startswith("s"):
            is_doubles = False
        else:
            is_doubles = cols["is_doubles_sheet"]

        primary = _cell(row, cols["player1"]) or _cell(row, cols["name"])
        partner = _cell(row, cols["player2"])

        if is_doubles and not partner:
            # A doubles sheet row missing its second player cannot form a team.
            if primary:
                errors.append(
                    "Row {}: '{}' has no second player, so no team could be formed.".format(line, primary)
                )
            continue

        if not primary:
            errors.append("Row {}: player name is missing.".format(line))
            continue

        club = _cell(row, cols["club"], "Independent")
        city = _cell(row, cols["city"], "Pune")
        rating = _int_cell(row, cols["rating"], 1500, errors,
                           "Row {} ({}): rating is not a number, defaulted to 1500.".format(line, primary))
        seed = _int_cell(row, cols["seed"], None, errors,
                         "Row {} ({}): seed is not a number, ignored.".format(line, primary))

        entry: Dict[str, Any] = {
            "type": "doubles" if is_doubles else "singles",
            "name": primary,
            "club": club,
            "city": city,
            "rating": rating,
            "seed": seed,
            "email": _cell(row, cols["email"]),
            "phone": _cell(row, cols["phone"]),
            "selected": True,
        }

        if is_doubles:
            entry["partnerName"] = partner
            entry["partnerEmail"] = _cell(row, cols["partner_email"])
            entry["partnerPhone"] = _cell(row, cols["partner_phone"])
            entry["teamName"] = _cell(row, cols["team"]) or "{} & {}".format(primary, partner)

        entries.append(entry)

    meta = {
        "detectedFormat": "doubles" if cols["is_doubles_sheet"] else "singles",
        "columnsDetected": {k: v for k, v in cols.items() if v and k != "is_doubles_sheet"},
        "singlesCount": sum(1 for e in entries if e["type"] == "singles"),
        "doublesCount": sum(1 for e in entries if e["type"] == "doubles"),
    }
    return entries, errors, meta
