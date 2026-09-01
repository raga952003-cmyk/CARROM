from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_db, get_admin_db
from app.models.tournament import (
    TournamentCreateSchema, TournamentUpdateSchema, RegistrationCreateSchema, ManualMatchSchema,
)
from app.utils.security import get_user_profile, verify_admin, get_optional_profile
from app.utils.serializers import (
    serialize_tournament,
    serialize_registration,
    serialize_match,
)
from app.services.fixture_engine import (
    generate_round_robin_fixtures,
    generate_knockout_bracket,
    generate_league_knockout_fixtures,
    generate_group_stage_fixtures,
    generate_group_knockout_fixtures,
    suggest_group_count,
)
from app.services.scheduling_engine import generate_conflict_free_schedule
from app.services.notification_service import fan_out_notification
from app.services.audit_service import record_audit
from app.services.access_control import (
    require_tournament_access,
    set_owner_on_create,
    strip_owner,
    describe_access,
)
from app.services.state_machine import (
    validate_tournament_transition,
    canonical_tournament_status,
    assert_tournament_accepts_registrations,
    set_tournament_status,
)
from typing import List, Dict, Any, Optional
import time
import uuid
import logging
import secrets

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/tournaments", tags=["tournaments"])

def _slot_id(value):
    """A bracket slot id, or None when the slot is not yet resolved."""
    if not value:
        return None
    if value in ("TBD", "Winner TBD", "__BYE__"):
        return None
    if str(value).startswith("qualifier_"):
        return None
    return value


def _select_all(query_factory, page: int = 1000) -> List[Dict[str, Any]]:
    """
    Read every row a query matches, not the first thousand.

    PostgREST caps an unpaginated response at 1000 rows and says nothing about
    it -- no error, no header the client checks, just a short list. Against 190
    matches of 8 boards that is 1520 rows arriving as 1000, so boards 7 and 8
    of every match simply did not exist as far as the app was concerned: the
    umpire scored up to board 5, the server activated board 6, and the screen
    had nothing to show. Under remaining-coins scoring, where a match completes
    only when every board is played, those matches could never be finished.

    `query_factory` is called for each page so the range can be applied to a
    fresh query object.
    """
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        batch = query_factory().range(offset, offset + page - 1).execute().data or []
        rows.extend(batch)
        # A short page is the last page. An exactly-full one might not be.
        if len(batch) < page:
            return rows
        offset += page


def _hydrate_registrations(supabase, reg_rows: List[Dict[str, Any]],
                           include_contact: bool = False) -> List[Dict[str, Any]]:
    """
    Attach both player profiles to every doubles registration's team.

    A PostgREST join reaches `teams` but not the two `profiles` rows it points
    at, so the team arrives with player1_id/player2_id and no names. The UI
    renders `team.player1.name` directly, so the profiles are fetched here in
    one batched query and grafted on.
    """
    if not reg_rows:
        return []

    team_player_ids = set()
    for reg in reg_rows:
        team = reg.get("team")
        if team:
            team_player_ids.update(
                pid for pid in (team.get("player1_id"), team.get("player2_id")) if pid
            )

    profiles_by_id: Dict[str, Any] = {}
    if team_player_ids:
        rows = supabase.table("profiles").select("*").in_(
            "id", list(team_player_ids)
        ).execute().data or []
        profiles_by_id = {p["id"]: p for p in rows}

    hydrated = []
    for reg in reg_rows:
        team = reg.get("team")
        if team:
            team["player1"] = profiles_by_id.get(team.get("player1_id"))
            team["player2"] = profiles_by_id.get(team.get("player2_id"))
        hydrated.append(serialize_registration(reg, include_contact))
    return hydrated


def _hydrate_tournaments(supabase, tournament_rows: List[Dict[str, Any]],
                         include_contact: bool = False) -> List[Dict[str, Any]]:
    """
    Attach registrations, matches, boards and audit history to tournament rows.

    Everything is fetched with one query per table (filtered by `in_`) and then
    grouped in memory, so hydrating N tournaments costs a constant number of
    round trips rather than N+1.
    """
    if not tournament_rows:
        return []

    tournament_ids = [t["id"] for t in tournament_rows]

    # --- Registrations (+ hydrated doubles partners) -----------------------
    reg_rows = _select_all(
        lambda: supabase.table("registrations")
        .select("*, player:profiles(*), team:teams(*)")
        .in_("tournament_id", tournament_ids)
    )

    regs_by_tournament: Dict[str, List[Dict[str, Any]]] = {}
    for raw, serialized in zip(reg_rows, _hydrate_registrations(supabase, reg_rows, include_contact)):
        regs_by_tournament.setdefault(raw["tournament_id"], []).append(serialized)

    # --- Matches, boards and score audit trail -----------------------------
    match_rows = _select_all(
        lambda: supabase.table("matches").select("*")
        .in_("tournament_id", tournament_ids).order("match_number")
    )

    match_ids = [m["id"] for m in match_rows]
    boards_by_match: Dict[str, List[Dict[str, Any]]] = {}
    audit_by_match: Dict[str, List[Dict[str, Any]]] = {}

    if match_ids:
        board_rows = _select_all(
            lambda: supabase.table("boards").select("*")
            .in_("match_id", match_ids).order("board_number")
        )
        for b in board_rows:
            boards_by_match.setdefault(b["match_id"], []).append(b)

        # Capped on purpose, and newest first. This table grows with every
        # score correction and is only ever displayed, so the whole history is
        # not worth carrying on the tournament list. The difference from the
        # reads above is that this limit is deliberate and stated, rather than
        # PostgREST quietly stopping at a thousand.
        audit_rows = supabase.table("score_audit_logs").select("*").in_(
            "match_id", match_ids
        ).order("timestamp", desc=True).limit(500).execute().data or []
        for a in audit_rows:
            audit_by_match.setdefault(a["match_id"], []).append(a)

    matches_by_tournament: Dict[str, List[Dict[str, Any]]] = {}
    for m in match_rows:
        matches_by_tournament.setdefault(m["tournament_id"], []).append(
            serialize_match(
                m,
                boards=boards_by_match.get(m["id"], []),
                audit_logs=audit_by_match.get(m["id"], []),
                # The list view carries every match of every tournament; the
                # single-tournament and single-match reads still send the lot.
                boards_with_play_only=True,
            )
        )

    return [
        serialize_tournament(
            t,
            registrations=regs_by_tournament.get(t["id"], []),
            matches=matches_by_tournament.get(t["id"], []),
        )
        for t in tournament_rows
    ]



# Migration 006 adds the set columns. Until it is applied a match is a flat
# list of boards exactly as before, so the columns are simply not written
# rather than the whole fixture generation failing on an unknown column.

# A missing column is cached only briefly. Caching "not supported" forever
# meant applying a migration did nothing at all until someone happened to
# restart the API — the feature stayed dark and the health check said fine.
# A column that exists cannot stop existing, so a positive result is kept.
_PROBE_RETRY_SECONDS = 30
_sets_supported: Dict[str, Any] = {}


# Generating a draw deletes every existing match before writing the new ones.
# Two runs at once therefore erase each other's work: the second delete removes
# what the first has inserted so far, both keep inserting, and the tournament
# ends up with duplicate match numbers and matches the UI is still pointing at
# that no longer exist ("Match not found" on the next action).
# In-process, so it guards a single server. On a serverless platform each
# request may land on a different instance and this will not see the other run
# — the real protections there are that the draw now takes about a second
# instead of minutes, and that the button disables while it is running.
_generating: Dict[str, float] = {}
_GENERATE_LOCK_SECONDS = 300


def _claim_generation(tournament_id: str) -> bool:
    """True if this caller may generate; False if a run is already under way."""
    now = time.monotonic()
    started = _generating.get(tournament_id)
    if started is not None and now - started < _GENERATE_LOCK_SECONDS:
        return False
    _generating[tournament_id] = now
    return True


def _release_generation(tournament_id: str) -> None:
    _generating.pop(tournament_id, None)


def sets_supported(admin_db) -> bool:
    cached = _sets_supported.get("value")
    if cached is True:
        return True
    if cached is False and time.monotonic() - _sets_supported.get("at", 0) < _PROBE_RETRY_SECONDS:
        return False
    try:
        admin_db.table("boards").select("set_number").limit(1).execute()
        _sets_supported["value"] = True
    except Exception:
        _sets_supported["value"] = False
        _sets_supported["at"] = time.monotonic()
    return _sets_supported["value"]


@router.get("")
async def get_tournaments(viewer = Depends(get_optional_profile)):
    # Reads run with the service client: this API layer performs its own
    # authorisation, and a missing RLS policy would otherwise return an empty
    # list rather than an error. RLS still governs direct client access,
    # including the Realtime stream.
    supabase = get_admin_db()
    try:
        rows = supabase.table("tournaments").select("*").order("created_at", desc=True).execute().data or []

        # A draft is the organiser's working copy: half-entered dates, a
        # placeholder name, entrants not yet approved. This endpoint answers
        # anonymously and the public board reads it, so a draft was published
        # the moment it was created. Invisible with one tournament; not once
        # next year's is being set up alongside this year's.
        is_admin = bool(viewer and viewer.get("role") == "admin")
        if not is_admin:
            rows = [t for t in rows if t.get("status") != "draft"]

        # Hydrated here because the dashboard reads tournament.matches and
        # tournament.registrations straight off the list response.
        # Contact details reach the organiser, never the public board.
        return _hydrate_tournaments(supabase, rows, is_admin)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{id}")
async def get_tournament(id: str, viewer = Depends(get_optional_profile)):
    supabase = get_admin_db()
    try:
        t_res = supabase.table("tournaments").select("*").eq("id", id).execute()
        if not t_res.data:
            raise HTTPException(status_code=404, detail="Tournament not found")
        is_admin = bool(viewer and viewer.get("role") == "admin")
        # Guessing the id of a draft should not be a way around the list filter.
        # 404 rather than 403: whether a draft exists is itself not public.
        if not is_admin and t_res.data[0].get("status") == "draft":
            raise HTTPException(status_code=404, detail="Tournament not found")
        return _hydrate_tournaments(supabase, t_res.data, is_admin)[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("")
async def create_tournament(data: TournamentCreateSchema, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        payload = {
            "name": data.name,
            "description": data.description,
            "category": data.category,
            "format": data.format,
            "registration_start_date": data.registration_start_date.isoformat(),
            "registration_end_date": data.registration_end_date.isoformat(),
            "tournament_start_date": data.tournament_start_date.isoformat(),
            "tournament_end_date": data.tournament_end_date.isoformat(),
            "venue": data.venue,
            "city": data.city,
            "number_of_boards": data.number_of_boards,
            "entry_fee": data.entry_fee,
            "prize_pool": data.prize_pool,
            "rules": data.rules.model_dump(by_alias=True),
            "poster_config": data.poster_config.model_dump(by_alias=True) if data.poster_config else {},
            "status": data.status or "draft"
        }
        # The creator owns it; other admins must request access (migration 003).
        try:
            res = admin_db.table("tournaments").insert(
                set_owner_on_create(payload, admin)).execute()
        except Exception as e:
            if "owner_id" not in str(e):
                raise
            res = admin_db.table("tournaments").insert(strip_owner(payload)).execute()
        created = res.data[0]
        record_audit(
            admin_db, actor=admin, action="tournament.create",
            entity_type="tournament", entity_id=created["id"],
            new_state=created,
        )
        return serialize_tournament(created)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{id}")
async def update_tournament(id: str, data: TournamentUpdateSchema, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        before = require_tournament_access(admin_db, id, admin, "tournament.update")

        update_dict = {}
        # Iterate over non-None values to build patch
        for key, val in data.model_dump(by_alias=True, exclude_unset=True).items():
            # Translate camelCase parameters to snake_case if writing directly
            if key == "registrationStartDate": update_dict["registration_start_date"] = val
            elif key == "registrationEndDate": update_dict["registration_end_date"] = val
            elif key == "tournamentStartDate": update_dict["tournament_start_date"] = val
            elif key == "tournamentEndDate": update_dict["tournament_end_date"] = val
            elif key == "numberOfBoards": update_dict["number_of_boards"] = val
            elif key == "entryFee": update_dict["entry_fee"] = val
            elif key == "prizePool": update_dict["prize_pool"] = val
            elif key == "posterConfig": update_dict["poster_config"] = val
            elif key == "schedulePublished": update_dict["schedule_published"] = val
            elif key == "fixturesGenerated": update_dict["fixtures_generated"] = val
            else:
                update_dict[key] = val
        
        # `rules` is one JSONB column, so writing it whole means an edit that
        # sets a single setting deletes every other one. The Rules tab sends
        # exactly that shape, which was silently resetting the queen value,
        # the scoring model and the group structure to their defaults.
        if "rules" in update_dict and isinstance(update_dict["rules"], dict):
            merged = dict(before.get("rules") or {})
            merged.update({k: v for k, v in update_dict["rules"].items() if v is not None})
            update_dict["rules"] = merged

        # Reject illegal lifecycle moves before touching the database (spec 75)
        if "status" in update_dict:
            validate_tournament_transition(before.get("status"), update_dict["status"])

        res = admin_db.table("tournaments").update(update_dict).eq("id", id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Tournament not found")

        record_audit(
            admin_db, actor=admin, action="tournament.update",
            entity_type="tournament", entity_id=id,
            previous_state=before, new_state=res.data[0],
            request_context={"changed_fields": sorted(update_dict.keys())},
        )
        return serialize_tournament(res.data[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{id}")
async def delete_tournament(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        before = [require_tournament_access(admin_db, id, admin, "tournament.delete")]

        admin_db.table("tournaments").delete().eq("id", id).execute()
        record_audit(
            admin_db, actor=admin, action="tournament.delete",
            entity_type="tournament", entity_id=id, previous_state=before[0],
        )
        return {"status": "success", "message": "Tournament deleted successfully."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{id}/registrations")
async def get_tournament_registrations(id: str, viewer = Depends(get_optional_profile)):
    supabase = get_admin_db()
    try:
        # Ordered so the registrations list does not reshuffle between refreshes.
        res = supabase.table("registrations").select(
            "*, player:profiles(*), team:teams(*)"
        ).eq("tournament_id", id).order("registered_at").order("id").execute()
        is_admin = bool(viewer and viewer.get("role") == "admin")
        return _hydrate_registrations(supabase, res.data or [], is_admin)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{id}/registrations")
async def register_for_tournament(id: str, data: RegistrationCreateSchema, profile = Depends(get_user_profile)):
    admin_db = get_admin_db()
    try:
        tournament = admin_db.table("tournaments").select("*").eq("id", id).execute().data
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found.")
        # Admins may enter participants at any stage; players only while open.
        if profile.get("role") != "admin":
            assert_tournament_accepts_registrations(tournament[0])

        player_id = data.player_id or profile["id"]
        team_id = None
        
        if data.type == "doubles":
            # A partner may be identified three ways: an existing profile id, an
            # existing email, or a name for someone with no account yet.
            if not data.partner_id and not data.partner_name:
                raise HTTPException(
                    status_code=400,
                    detail="Select an existing partner or enter a partner name for doubles registration.",
                )

            if data.partner_id:
                partner_res = admin_db.table("profiles").select("*").eq(
                    "id", data.partner_id
                ).execute()
                if not partner_res.data:
                    raise HTTPException(status_code=404, detail="Selected partner profile was not found.")
                partner_profile = partner_res.data[0]
            else:
                # Find by email first so a repeated entry reuses the same person
                # instead of creating a duplicate profile.
                partner_profile = None
                if data.partner_email:
                    existing = admin_db.table("profiles").select("*").eq(
                        "email", data.partner_email
                    ).execute().data
                    if existing:
                        partner_profile = existing[0]

                if partner_profile is None:
                    partner_email = data.partner_email or f"partner_{uuid.uuid4().hex[:8]}@carromarena.com"
                    auth_user = admin_db.auth.admin.create_user({
                        "email": partner_email,
                        "password": secrets.token_urlsafe(32),
                        "email_confirm": True,
                        "user_metadata": {
                            "name": data.partner_name,
                            "role": "player",
                            "club": profile.get("club", "Independent"),
                            "city": profile.get("city")
                        }
                    })
                    admin_db.auth.admin.update_user_by_id(
                        auth_user.user.id,
                        attributes={"app_metadata": {"role": "player"}}
                    )
                    if data.partner_phone:
                        admin_db.table("profiles").update(
                            {"phone": data.partner_phone}
                        ).eq("id", auth_user.user.id).execute()

                    partner_profile = admin_db.table("profiles").select("*").eq(
                        "id", auth_user.user.id
                    ).execute().data[0]

            if partner_profile["id"] == player_id:
                raise HTTPException(
                    status_code=422,
                    detail="A player cannot be their own doubles partner.",
                )

            # Find or create team
            # Check if team already exists for these 2 players
            team_res = admin_db.table("teams").select("*").or_(
                f"and(player1_id.eq.{player_id},player2_id.eq.{partner_profile['id']}),"
                f"and(player1_id.eq.{partner_profile['id']},player2_id.eq.{player_id})"
            ).execute()
            
            if team_res.data:
                team_id = team_res.data[0]["id"]
            else:
                player1_profile = admin_db.table("profiles").select("name, club, city").eq(
                    "id", player_id
                ).execute().data
                player1_name = player1_profile[0]["name"] if player1_profile else "Player 1"

                team_payload = {
                    "name": data.team_name or f"{player1_name} & {partner_profile['name']}",
                    "player1_id": player_id,
                    "player2_id": partner_profile["id"],
                    "club": (player1_profile[0].get("club") if player1_profile else None) or profile.get("club"),
                    "city": (player1_profile[0].get("city") if player1_profile else None) or profile.get("city"),
                }
                new_team = admin_db.table("teams").insert(team_payload).execute()
                team_id = new_team.data[0]["id"]
            
            # Reset player_id to NULL since it's a team registration
            player_id = None

        # Create registration record
        reg_payload = {
            "tournament_id": id,
            "type": data.type,
            "player_id": player_id,
            "team_id": team_id,
            "status": "approved" if profile.get("role") == "admin" else "pending", # auto-approve if admin registering them
            "payment_status": "pending",
            "notes": data.notes
        }
        res = admin_db.table("registrations").insert(reg_payload).execute()
        return serialize_registration(res.data[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{id}/fixtures")
async def generate_fixtures(id: str, force: bool = Query(False,
                            description="Discard results already recorded and redraw anyway."),
                            admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    if not _claim_generation(id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Fixtures are already being generated for this tournament. "
                "Wait for that to finish before starting another draw."
            ),
        )
    try:
        t = require_tournament_access(admin_db, id, admin, "tournament.fixtures")

        # Load approved registrations
        reg_res = admin_db.table("registrations").select("*, player:profiles(*), team:teams(*)").eq("tournament_id", id).eq("status", "approved").execute()
        regs = reg_res.data
        if len(regs) < 2:
            raise HTTPException(status_code=400, detail="Cannot generate fixtures with fewer than 2 approved participants.")

        # Map participants format
        # Singles players and doubles teams are separate competitions even
        # inside one tournament. Pooling them together produced a draw in which
        # a single player was fixtured against a two-person team.
        pools: Dict[str, List[Dict[str, Any]]] = {"singles": [], "doubles": []}
        for r in regs:
            if r["type"] == "singles" and r.get("player"):
                pools["singles"].append(r["player"])
            elif r["type"] == "doubles" and r.get("team"):
                pools["doubles"].append(r["team"])

        max_boards = t.get("rules", {}).get("maxBoardsPerMatch", 8)
        number_of_sets = int(t.get("rules", {}).get("numberOfSets") or 1)
        boards_per_set = int(t.get("rules", {}).get("boardsPerSet") or max_boards)
        if not sets_supported(admin_db):
            number_of_sets = 1
        # boardsPerSet governs the boards in a match whether or not the
        # tournament is played in sets: with a single set, that set IS the
        # match. It used to apply only when numberOfSets > 1, so a tournament
        # configured for 8 boards a match quietly got 3 -- the default of
        # maxBoardsPerMatch -- while the screen went on reading "0 / 8" from the
        # rule that had been ignored.
        if boards_per_set:
            max_boards = boards_per_set
        format_type = t.get("format")

        rules = t.get("rules") or {}
        # Groups are requested either by the format name, or by setting
        # groupCount on a league format. The second route works on databases
        # that predate the group formats being added to the format CHECK.
        requested_groups = rules.get("groupCount") or rules.get("group_count")
        wants_groups = bool(requested_groups and int(requested_groups) > 1) or \
            format_type in ("group_stage", "group_knockout")
        qualifiers_per_group = int(rules.get("qualifiersPerGroup") or 2)

        def build(pool: List[Dict[str, Any]], prefix: str) -> List[Dict[str, Any]]:
            group_count = int(requested_groups) if requested_groups else suggest_group_count(len(pool))

            if wants_groups:
                if format_type in ("knockout",):
                    # Groups make no sense without a league phase.
                    return generate_knockout_bracket(id, pool, max_boards, number_of_sets=number_of_sets, id_prefix=prefix + "ko")
                if format_type in ("group_knockout", "league_knockout"):
                    return generate_group_knockout_fixtures(
                        id, pool, max_boards, number_of_sets=number_of_sets, group_count=group_count,
                        qualifiers_per_group=qualifiers_per_group, id_prefix=prefix + "gk")
                return generate_group_stage_fixtures(
                    id, pool, max_boards, number_of_sets=number_of_sets, group_count=group_count, id_prefix=prefix + "gs")

            if format_type == "round_robin":
                return generate_round_robin_fixtures(id, pool, max_boards, number_of_sets=number_of_sets, id_prefix=prefix + "rr")
            if format_type == "knockout":
                return generate_knockout_bracket(id, pool, max_boards, number_of_sets=number_of_sets, id_prefix=prefix + "ko")
            return generate_league_knockout_fixtures(id, pool, max_boards, number_of_sets=number_of_sets, id_prefix=prefix)

        matches = []
        per_category: Dict[str, int] = {}
        for category in ("singles", "doubles"):
            pool = pools[category]
            if len(pool) < 2:
                if len(pool) == 1:
                    logger.warning(
                        f"Tournament {id}: only one {category} entrant, no {category} fixtures drawn."
                    )
                continue
            drawn = build(pool, category[0])
            for m in drawn:
                m["type"] = category
            per_category[category] = len(drawn)
            matches.extend(drawn)

        if not matches:
            raise HTTPException(
                status_code=400,
                detail="Cannot generate fixtures: no category has at least 2 approved participants.",
            )

        # One continuous numbering across both categories.
        for i, m in enumerate(matches, start=1):
            m["matchNumber"] = i

        # Regenerating replaces the draw, and this delete cascades to every
        # board and every score on it. That is fine before play starts and
        # catastrophic after: the button sits beside Auto-Schedule and Publish,
        # and one misplaced click would erase a day's results with no undo.
        existing = admin_db.table("matches").select(
            "id, result_confirmed, status"
        ).eq("tournament_id", id).execute().data or []
        confirmed = [m for m in existing if m.get("result_confirmed")]
        completed = [m for m in existing if m.get("status") == "completed"]
        if (confirmed or completed) and not force:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This draw already has {} confirmed and {} completed match(es), and "
                    "regenerating deletes every match and every board score with them. "
                    "Add a match instead if someone joined late, or confirm you want the "
                    "results discarded."
                ).format(len(confirmed), len(completed)),
            )

        admin_db.table("matches").delete().eq("tournament_id", id).execute()

        # Insert new matches & boards
        match_rows: List[Dict[str, Any]] = []
        board_rows: List[Dict[str, Any]] = []
        for match in matches:
            match_payload = {
                "id": str(uuid.uuid4()),  # Convert string mock IDs to true UUIDs
                "tournament_id": id,
                "match_number": match["matchNumber"],
                "round_name": match["roundName"],
                "round_index": match["roundIndex"],
                "stage": match["stage"],
                "type": match["type"],
                # Empty slots (byes, "Winner TBD", unresolved league ranks) must
                # reach the database as NULL: the column is a UUID.
                "player1_id": _slot_id(match.get("player1Id")),
                "player2_id": _slot_id(match.get("player2Id")),
                "player1_name": match["player1Name"],
                "player2_name": match["player2Name"],
                "board_number": match["boardNumber"],
                "status": "scheduled",
                "max_boards": match["maxBoards"],
                "target_points": t.get("rules", {}).get("targetScore", 29),
                # Carries the group label for group formats and the draw slot
                # for knockouts; JSONB, so no schema change was needed.
                "bracket_position": match.get("bracketPosition")
            }
            if "nextMatchId" in match:
                # Note: We will update bracket linking parent IDs later since they must refer to database UUIDs
                pass
            
            if number_of_sets > 1:
                match_payload["number_of_sets"] = number_of_sets

            # The id is generated here, so the boards can be built now and the
            # whole draw written in a handful of round trips instead of one per
            # row. A 63-match draw was 63 match inserts plus 500-odd board
            # inserts, which took long enough that organisers clicked Generate
            # again and got a second draw on top of the first.
            match_id = match_payload["id"]
            match["db_uuid"] = match_id
            match_rows.append(match_payload)

            for board in match["boards"]:
                set_number = board.get("setNumber", 1)
                board_payload = {
                    "match_id": match_id,
                    "board_number": board["boardNumber"],
                    "status": "in_progress" if (set_number == 1 and board["boardNumber"] == 1)
                              else "pending",
                    "player1_score": 0,
                    "player2_score": 0
                }
                if number_of_sets > 1:
                    board_payload["set_number"] = set_number
                board_rows.append(board_payload)

        def insert_in_chunks(table: str, rows: List[Dict[str, Any]], size: int = 200) -> None:
            for start in range(0, len(rows), size):
                admin_db.table(table).insert(rows[start:start + size]).execute()

        try:
            insert_in_chunks("matches", match_rows)
            insert_in_chunks("boards", board_rows)
        except Exception as e:
            # A board whose match has vanished mid-write means another draw ran
            # at the same time and deleted it. The in-process guard cannot see a
            # run on a different serverless instance, so this is the backstop:
            # say what happened instead of surfacing a raw constraint violation.
            if "boards_match_id_fkey" in str(e) or "23503" in str(e):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Another draw was generated for this tournament while this "
                        "one was being written, so this draw was abandoned. "
                        "Reload the fixtures and generate once more if they look wrong."
                    ),
                )
            raise
        logger.info(
            "Fixtures for %s: %d matches, %d boards written in %d request(s).",
            id, len(match_rows), len(board_rows),
            -(-len(match_rows) // 200) + -(-len(board_rows) // 200),
        )

        # Update knockout parent references with database-assigned UUIDs
        if format_type in ("knockout", "league_knockout", "hybrid"):
            # Re-map mock IDs to database UUIDs
            id_map = {m["id"]: m["db_uuid"] for m in matches}
            for match in matches:
                if "nextMatchId" in match and match["nextMatchId"] in id_map:
                    next_uuid = id_map[match["nextMatchId"]]
                    admin_db.table("matches").update({
                        "next_match_id": next_uuid,
                        "next_match_slot": match.get("nextMatchSlot")
                    }).eq("id", match["db_uuid"]).execute()

        # The flag write must always succeed; the lifecycle advance is applied
        # separately so a state name the database does not yet accept cannot
        # roll back a successful draw (spec 75).
        admin_db.table("tournaments").update({"fixtures_generated": True}).eq("id", id).execute()

        new_status = None
        if canonical_tournament_status(t.get("status")) in ("registration_closed", "fixture_generation"):
            new_status = set_tournament_status(admin_db, id, "fixture_published")

        record_audit(
            admin_db, actor=admin, action="tournament.generate_fixtures",
            entity_type="tournament", entity_id=id,
            previous_state={"status": t.get("status"), "fixtures_generated": t.get("fixtures_generated")},
            new_state={"status": new_status, "fixtures_generated": True},
            request_context={"format": format_type, "match_count": len(matches)},
        )
        breakdown = ", ".join(f"{n} {c}" for c, n in per_category.items())
        return {
            "status": "success",
            "message": f"Generated {len(matches)} fixtures ({breakdown}).",
            "byCategory": per_category,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        # Released whether the draw succeeded or failed, so a failure does not
        # lock the tournament out of ever generating again.
        _release_generation(id)

@router.post("/{id}/schedule")
async def generate_schedule(id: str, restMinutes: int = Query(10), admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        t = require_tournament_access(admin_db, id, admin, "tournament.schedule")

        # Load all matches
        matches_res = admin_db.table("matches").select("*").eq("tournament_id", id).execute()
        matches = matches_res.data
        if not matches:
            raise HTTPException(status_code=400, detail="No fixtures generated to schedule.")

        # Structure matches for engine input
        # Resolve every doubles side to the two people on it, so a player who
        # entered both categories is never scheduled onto two boards at once.
        team_ids = {
            m[k] for m in matches for k in ("player1_id", "player2_id")
            if m.get(k) and m.get("type") == "doubles"
        }
        team_members: Dict[str, List[str]] = {}
        if team_ids:
            for team in (admin_db.table("teams").select("id, player1_id, player2_id").in_(
                    "id", list(team_ids)).execute().data or []):
                team_members[team["id"]] = [
                    pid for pid in (team.get("player1_id"), team.get("player2_id")) if pid
                ]

        engine_matches = []
        for m in matches:
            people: List[str] = []
            for key in ("player1_id", "player2_id"):
                side = m.get(key)
                if not side:
                    continue
                people.extend(team_members.get(side, [side]))

            engine_matches.append({
                "id": m["id"],
                "player1Id": m.get("player1_id"),
                "player2Id": m.get("player2_id"),
                "participantIds": people,
                "stage": m["stage"],
                "roundIndex": m["round_index"],
                "boardNumber": m["board_number"]
            })

        num_boards = t["number_of_boards"]
        start_date = t["tournament_start_date"]
        duration = t.get("rules", {}).get("matchDurationMinutes", 30)

        scheduled = generate_conflict_free_schedule(
            engine_matches, 
            number_of_boards=num_boards, 
            start_date=start_date, 
            match_duration_minutes=duration, 
            rest_time_minutes=restMinutes
        )

        # Update matches in db
        for s_match in scheduled:
            update_payload = {
                "board_number": s_match["boardNumber"],
                "scheduled_date": s_match["scheduledDate"],
                "scheduled_time": s_match["scheduledTime"]
            }
            admin_db.table("matches").update(update_payload).eq("id", s_match["id"]).execute()

        return {"status": "success", "message": f"Conflict-free schedule generated across {num_boards} boards."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{id}/publish-schedule")
async def publish_schedule(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        require_tournament_access(admin_db, id, admin, "tournament.publish")
        admin_db.table("tournaments").update({"schedule_published": True}).eq("id", id).execute()
        
        # Load tournament name
        t = admin_db.table("tournaments").select("name").eq("id", id).execute().data[0]
        
        # One row per recipient so each participant can mark it read themselves
        delivered = fan_out_notification(
            admin_db,
            title="Match Schedule Published!",
            message=f"The official match schedule for the '{t['name']}' tournament is now published. Check your boards and timings!",
            type="schedule_published",
            tournament_id=id,
        )

        record_audit(
            admin_db, actor=admin, action="tournament.publish_schedule",
            entity_type="tournament", entity_id=id,
            new_state={"schedule_published": True},
            request_context={"notified": delivered},
        )
        return {
            "status": "success",
            "message": f"Schedule published and notified {delivered} participant(s)."
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{id}/matches")
async def add_manual_match(id: str, data: ManualMatchSchema, admin = Depends(verify_admin)):
    """
    Add a single fixture to a tournament that already has a draw.

    Regenerating fixtures rebuilds every match and discards the boards already
    scored, so it is not an option once play has started. This creates one match
    beside the existing ones, with the boards and sets the tournament's own
    rules call for.
    """
    admin_db = get_admin_db()
    try:
        t = require_tournament_access(admin_db, id, admin, "tournament.manage")

        if data.player1_id == data.player2_id:
            raise HTTPException(status_code=422, detail="A player cannot be fixtured against themselves.")
        if data.stage not in ("league", "knockout"):
            raise HTTPException(status_code=422, detail="Stage must be 'league' or 'knockout'.")

        # Both sides have to be in this tournament, and approved.
        regs = admin_db.table("registrations").select(
            "*, player:profiles(*), team:teams(*)"
        ).eq("tournament_id", id).eq("status", "approved").execute().data or []

        entrants: Dict[str, Dict[str, Any]] = {}
        for r in regs:
            if r["type"] == "singles" and r.get("player"):
                entrants[r["player"]["id"]] = {"name": r["player"]["name"], "type": "singles"}
            elif r["type"] == "doubles" and r.get("team"):
                entrants[r["team"]["id"]] = {"name": r["team"]["name"], "type": "doubles"}

        for slot, pid in (("Player 1", data.player1_id), ("Player 2", data.player2_id)):
            if pid not in entrants:
                raise HTTPException(
                    status_code=422,
                    detail=f"{slot} is not an approved entrant in this tournament.",
                )
        p1, p2 = entrants[data.player1_id], entrants[data.player2_id]
        if p1["type"] != p2["type"]:
            raise HTTPException(
                status_code=422,
                detail="A singles player cannot be fixtured against a doubles team.",
            )

        rules = t.get("rules") or {}
        max_boards = int(rules.get("maxBoardsPerMatch") or 8)
        number_of_sets = int(rules.get("numberOfSets") or 1)
        boards_per_set = int(rules.get("boardsPerSet") or max_boards)
        if not sets_supported(admin_db):
            number_of_sets = 1
        # Same rule as generated fixtures, so a manually added match is the
        # same length as the rest of the draw.
        if boards_per_set:
            max_boards = boards_per_set

        existing = admin_db.table("matches").select("match_number, round_index").eq(
            "tournament_id", id).execute().data or []
        next_number = max((m.get("match_number") or 0) for m in existing) + 1 if existing else 1
        round_index = max((m.get("round_index") or 0) for m in existing) if existing else 0

        match_payload = {
            "id": str(uuid.uuid4()),
            "tournament_id": id,
            "match_number": next_number,
            "round_name": data.round_name or ("Knockout" if data.stage == "knockout" else "League"),
            # Sits at the end of the draw so it never reorders existing rounds.
            "round_index": round_index,
            "stage": data.stage,
            "type": p1["type"],
            "player1_id": data.player1_id,
            "player2_id": data.player2_id,
            "player1_name": p1["name"],
            "player2_name": p2["name"],
            "board_number": data.board_number or 1,
            "status": "scheduled",
            "max_boards": max_boards,
            "target_points": rules.get("targetScore", 29),
            # Marked so it is distinguishable from a drawn fixture later.
            "bracket_position": {"manual": True, "addedBy": admin.get("name")},
        }
        if data.scheduled_date:
            match_payload["scheduled_date"] = data.scheduled_date
        if data.scheduled_time:
            match_payload["scheduled_time"] = data.scheduled_time
        if number_of_sets > 1:
            match_payload["number_of_sets"] = number_of_sets

        created = admin_db.table("matches").insert(match_payload).execute()
        match_id = created.data[0]["id"]

        for set_number in range(1, number_of_sets + 1):
            for board_number in range(1, max_boards + 1):
                board = {
                    "match_id": match_id,
                    "board_number": board_number,
                    "status": "in_progress" if (set_number == 1 and board_number == 1) else "pending",
                    "player1_score": 0,
                    "player2_score": 0,
                }
                if number_of_sets > 1:
                    board["set_number"] = set_number
                admin_db.table("boards").insert(board).execute()

        record_audit(
            admin_db, actor=admin, action="match.added_manually",
            entity_type="match", entity_id=match_id,
            new_state={"round": match_payload["round_name"],
                       "player1": p1["name"], "player2": p2["name"]},
            request_context={"tournament_id": id},
        )

        rows = admin_db.table("matches").select("*").eq("id", match_id).execute().data
        boards = admin_db.table("boards").select("*").eq("match_id", match_id).order("board_number").execute().data
        return serialize_match(rows[0], boards or [])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
