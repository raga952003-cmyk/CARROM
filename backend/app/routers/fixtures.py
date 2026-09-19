"""
Fixtures domain (spec 61, 68).

Fixture generation itself lives in `services/fixture_engine.py` and is applied by
`routers/tournaments.generate_fixtures`. This router exposes it under its own
domain path and adds the read side, which the tournament router never had.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_db, get_admin_db
from app.utils.security import verify_admin
from app.services.access_control import require_tournament_access
from app.utils.serializers import serialize_match
from app.utils.idempotency import IdempotencyGuard, get_idempotency_key
from app.routers.tournaments import (
    generate_fixtures as _generate_fixtures,
    _select_all,
    _slot_id,
    sets_supported,
)
from app.routers.standings import compute_standings
from app.services.fixture_engine import generate_knockout_bracket, QUALIFIER_PREFIX
from app.services.qualification import (
    knockout_has_started,
    league_is_complete,
    promote_qualifiers,
)
from app.services.audit_service import record_audit
from typing import Any, Dict, List, Optional
import uuid

router = APIRouter(prefix="/fixtures", tags=["fixtures"])


@router.get("/{tournament_id}")
async def list_fixtures(
    tournament_id: str,
    stage: Optional[str] = Query(None, pattern="^(league|knockout)$"),
    round_index: Optional[int] = Query(None, ge=1),
):
    """Generated fixtures for a tournament, optionally filtered by stage/round."""
    supabase = get_admin_db()
    try:
        query = supabase.table("matches").select("*").eq("tournament_id", tournament_id)
        if stage:
            query = query.eq("stage", stage)
        if round_index is not None:
            query = query.eq("round_index", round_index)

        matches = query.order("match_number").execute().data or []
        match_ids = [m["id"] for m in matches]

        boards_by_match = {}
        if match_ids:
            boards = supabase.table("boards").select("*").in_(
                "match_id", match_ids
            ).order("board_number").execute().data or []
            for b in boards:
                boards_by_match.setdefault(b["match_id"], []).append(b)

        return [serialize_match(m, boards=boards_by_match.get(m["id"], [])) for m in matches]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{tournament_id}/generate")
async def generate(
    tournament_id: str,
    # Same switch the tournaments route has, so the two paths to a redraw
    # behave alike: without it a draw with play recorded on it is refused
    # with 409, and this route would have had no way to say "yes, discard it".
    force: bool = Query(False,
                        description="Discard results already recorded and redraw anyway."),
    admin = Depends(verify_admin),
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Deterministic fixture generation (spec 68). Regenerating replaces the
    previous draw, so this is guarded by an optional Idempotency-Key.
    """
    # Regenerating replaces someone's entire draw, so it belongs to whoever
    # runs that tournament, not to any admin who knows its id.
    require_tournament_access(get_admin_db(), tournament_id, admin)

    # `force` is part of the request identity: replaying a key that drew
    # cautiously must not be accepted as consent to discard results.
    guard = IdempotencyGuard(
        get_admin_db(), idempotency_key,
        f"POST /fixtures/{tournament_id}/generate",
        {"tournament_id": tournament_id, "force": force},
    )
    cached = guard.replay()
    if cached is not None:
        return cached

    # Keyword arguments, deliberately. generate_fixtures is (id, force, admin)
    # and this used to call it as (tournament_id, admin): the admin profile
    # slid into `force` and `admin` was left holding FastAPI's Depends marker,
    # which require_tournament_access then tried to read a role off -- every
    # call through this route failed with a 400 that named no cause. Passing
    # `force` explicitly matters too: left to its default it would be the
    # Query() descriptor, which is truthy, and the guard against deleting
    # recorded results would silently never fire.
    result = await _generate_fixtures(id=tournament_id, force=force, admin=admin)
    guard.store(result)
    return result


@router.post("/{tournament_id}/knockout")
async def add_knockout_stage(
    tournament_id: str,
    slots: int = Query(8, ge=2, le=32,
                       description="How many league finishers the bracket takes."),
    replace: bool = Query(False,
                          description="Discard an existing, unplayed knockout stage and redraw it."),
    admin = Depends(verify_admin),
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Append a knockout bracket to a draw that already has a league.

    `generate_fixtures` is the only other way to get a bracket and it rebuilds
    the whole draw, deleting every board and score on it -- not an option for a
    round robin that has already been played. This adds the knockout matches
    beside the league and touches nothing that exists.

    Slots start empty, labelled "League Rank #n", exactly as a league_knockout
    draw leaves them, so `POST /standings/{id}/promote` resolves them against
    the official standings and `qualifying_count` reads the seats back off the
    bracket. `generate_league_knockout_fixtures` caps its bracket at four
    qualifiers, which is why it cannot draw a quarter-final and is not used here.
    """
    admin_db = get_admin_db()

    # A redraw discards matches, so it is part of the request identity: a key
    # replayed from a cautious call must not be read as consent to replace.
    guard = IdempotencyGuard(
        admin_db, idempotency_key,
        f"POST /fixtures/{tournament_id}/knockout",
        {"tournament_id": tournament_id, "slots": slots, "replace": replace},
    )
    cached = guard.replay()
    if cached is not None:
        return cached

    t = require_tournament_access(admin_db, tournament_id, admin, "tournament.fixtures")

    # A bracket that is not a power of two gives the top seeds byes. That is
    # right for a knockout drawn from entrants and wrong for one drawn from a
    # league table, where a bye hands rank 1 a free round for no visible reason.
    if slots & (slots - 1):
        raise HTTPException(
            status_code=422,
            detail=(
                f"A bracket needs a power-of-two number of slots; {slots} would give "
                "the top seeds byes. Use 2, 4, 8, 16 or 32."
            ),
        )

    matches = _select_all(
        lambda: admin_db.table("matches").select("*").eq("tournament_id", tournament_id)
    )
    if not matches:
        raise HTTPException(status_code=404, detail="This tournament has no fixtures yet.")

    league = [m for m in matches if m.get("stage") == "league"]
    if not league:
        raise HTTPException(
            status_code=409,
            detail="This tournament has no league stage to promote out of.",
        )

    ranked = len({
        pid for m in league
        for pid in (m.get("player1_id"), m.get("player2_id")) if pid
    })
    if ranked < slots:
        raise HTTPException(
            status_code=422,
            detail=(
                f"The league has {ranked} participant(s), so it cannot fill {slots} "
                "knockout slots."
            ),
        )

    existing_ko = [m for m in matches if m.get("stage") == "knockout"]
    if existing_ko and not replace:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This tournament already has a knockout stage ({len(existing_ko)} matches). "
                "Promote into it, or pass replace=true to redraw it."
            ),
        )
    if existing_ko and knockout_has_started(matches):
        # Not overridable. Past this point a redraw is deleting results.
        raise HTTPException(
            status_code=409,
            detail="The knockout stage has already been played; redrawing it would delete results.",
        )

    rules = t.get("rules") or {}
    max_boards = int(rules.get("maxBoardsPerMatch") or 8)
    number_of_sets = int(rules.get("numberOfSets") or 1)
    boards_per_set = int(rules.get("boardsPerSet") or max_boards)
    if not sets_supported(admin_db):
        number_of_sets = 1
    # Same rule the league was drawn under, so a knockout match is the same
    # length as the matches that fed it.
    if boards_per_set:
        max_boards = boards_per_set

    # One bracket per category that actually has a league, so a tournament
    # running singles and doubles side by side gets one of each rather than a
    # singles bracket the doubles table is then promoted into.
    categories = sorted({(m.get("type") or "singles") for m in league})

    drawn: List[Dict[str, Any]] = []
    for category in categories:
        placeholders = [
            {"id": f"{QUALIFIER_PREFIX}{i}", "name": f"League Rank #{i}", "seed": i}
            for i in range(1, slots + 1)
        ]
        bracket = generate_knockout_bracket(
            tournament_id, placeholders, max_boards,
            number_of_sets=number_of_sets,
            id_prefix=f"{category[0]}ko{slots}",
        )
        for m in bracket:
            m["type"] = category
        drawn.extend(bracket)

    if not drawn:
        raise HTTPException(status_code=400, detail="Could not draw a bracket for that many slots.")

    # The league keeps its numbers; the knockout continues past the end of it.
    next_number = max((m.get("match_number") or 0) for m in matches) + 1
    round_offset = max((m.get("round_index") or 0) for m in league)
    for i, m in enumerate(drawn):
        m["matchNumber"] = next_number + i
        # Rounds are read in order across the whole draw, so the knockout has
        # to sit after the last league round rather than restart at 1.
        m["roundIndex"] = round_offset + m["roundIndex"]

    venue_boards = max(1, int(t.get("number_of_boards") or 1))
    match_rows: List[Dict[str, Any]] = []
    board_rows: List[Dict[str, Any]] = []
    for i, match in enumerate(drawn):
        match_id = str(uuid.uuid4())
        match["db_uuid"] = match_id
        payload = {
            "id": match_id,
            "tournament_id": tournament_id,
            "match_number": match["matchNumber"],
            "round_name": match["roundName"],
            "round_index": match["roundIndex"],
            "stage": "knockout",
            "type": match["type"],
            # Placeholders reach the database as NULL; the label in the name
            # column is what promotion resolves.
            "player1_id": _slot_id(match.get("player1Id")),
            "player2_id": _slot_id(match.get("player2Id")),
            "player1_name": match["player1Name"],
            "player2_name": match["player2Name"],
            "board_number": (i % venue_boards) + 1,
            "status": "scheduled",
            "max_boards": match["maxBoards"],
            "target_points": rules.get("targetScore", 29),
            "bracket_position": match.get("bracketPosition"),
        }
        if number_of_sets > 1:
            payload["number_of_sets"] = number_of_sets
        match_rows.append(payload)

        for board in match["boards"]:
            set_number = board.get("setNumber", 1)
            board_payload = {
                "match_id": match_id,
                "board_number": board["boardNumber"],
                "status": "in_progress" if (set_number == 1 and board["boardNumber"] == 1)
                          else "pending",
                "player1_score": 0,
                "player2_score": 0,
            }
            if number_of_sets > 1:
                board_payload["set_number"] = set_number
            board_rows.append(board_payload)

    replaced = 0
    if existing_ko:
        ids = [m["id"] for m in existing_ko]
        for start in range(0, len(ids), 100):
            admin_db.table("matches").delete().in_("id", ids[start:start + 100]).execute()
        replaced = len(existing_ko)

    for start in range(0, len(match_rows), 200):
        admin_db.table("matches").insert(match_rows[start:start + 200]).execute()
    for start in range(0, len(board_rows), 200):
        admin_db.table("boards").insert(board_rows[start:start + 200]).execute()

    # Engine ids are strings; the links have to carry the database UUIDs.
    id_map = {m["id"]: m["db_uuid"] for m in drawn}
    for match in drawn:
        if match.get("nextMatchId") in id_map:
            admin_db.table("matches").update({
                "next_match_id": id_map[match["nextMatchId"]],
                "next_match_slot": match.get("nextMatchSlot"),
            }).eq("id", match["db_uuid"]).execute()

    # Seed it now if the league is already decided; otherwise the labels wait,
    # and confirming the last league result promotes them automatically.
    complete, confirmed, total = league_is_complete(matches)
    promotion = None
    if complete:
        seeded = _select_all(
            lambda: admin_db.table("matches").select("*").eq("tournament_id", tournament_id)
        )
        promotion = promote_qualifiers(
            admin_db, tournament_id, compute_standings(admin_db, tournament_id), seeded)

    record_audit(
        admin_db, actor=admin, action="tournament.add_knockout_stage",
        entity_type="tournament", entity_id=tournament_id,
        new_state={"slots": slots, "matches": len(match_rows), "replaced": replaced},
        request_context={"leagueConfirmed": f"{confirmed}/{total}", "promoted": bool(promotion)},
    )

    result = {
        "status": "success",
        "message": (
            f"Drew {len(match_rows)} knockout match(es) for the top {slots} of each "
            f"league table ({', '.join(categories)})."
        ),
        "slots": slots,
        "matchesCreated": len(match_rows),
        "matchesReplaced": replaced,
        "rounds": sorted({m["roundName"] for m in drawn}),
        "leagueConfirmed": f"{confirmed}/{total}",
        "leagueComplete": complete,
        "promotion": promotion,
        "nextStep": (
            "Slots are seeded from the standings."
            if promotion else
            f"The league is at {confirmed}/{total}. Confirm the rest and the slots fill "
            f"automatically, or POST /standings/{tournament_id}/promote?force=true to seed now."
        ),
    }
    guard.store(result)
    return result
