from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_db, get_admin_db
from app.models.tournament import TournamentCreateSchema, TournamentUpdateSchema, RegistrationCreateSchema
from app.utils.security import get_user_profile, verify_admin
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
from app.services.state_machine import (
    validate_tournament_transition,
    canonical_tournament_status,
    assert_tournament_accepts_registrations,
    set_tournament_status,
)
from typing import List, Dict, Any, Optional
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


def _hydrate_registrations(supabase, reg_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
        hydrated.append(serialize_registration(reg))
    return hydrated


def _hydrate_tournaments(supabase, tournament_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
    reg_rows = supabase.table("registrations").select(
        "*, player:profiles(*), team:teams(*)"
    ).in_("tournament_id", tournament_ids).execute().data or []

    regs_by_tournament: Dict[str, List[Dict[str, Any]]] = {}
    for raw, serialized in zip(reg_rows, _hydrate_registrations(supabase, reg_rows)):
        regs_by_tournament.setdefault(raw["tournament_id"], []).append(serialized)

    # --- Matches, boards and score audit trail -----------------------------
    match_rows = supabase.table("matches").select("*").in_(
        "tournament_id", tournament_ids
    ).order("match_number").execute().data or []

    match_ids = [m["id"] for m in match_rows]
    boards_by_match: Dict[str, List[Dict[str, Any]]] = {}
    audit_by_match: Dict[str, List[Dict[str, Any]]] = {}

    if match_ids:
        board_rows = supabase.table("boards").select("*").in_(
            "match_id", match_ids
        ).order("board_number").execute().data or []
        for b in board_rows:
            boards_by_match.setdefault(b["match_id"], []).append(b)

        audit_rows = supabase.table("score_audit_logs").select("*").in_(
            "match_id", match_ids
        ).order("timestamp", desc=True).execute().data or []
        for a in audit_rows:
            audit_by_match.setdefault(a["match_id"], []).append(a)

    matches_by_tournament: Dict[str, List[Dict[str, Any]]] = {}
    for m in match_rows:
        matches_by_tournament.setdefault(m["tournament_id"], []).append(
            serialize_match(
                m,
                boards=boards_by_match.get(m["id"], []),
                audit_logs=audit_by_match.get(m["id"], []),
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


@router.get("")
async def get_tournaments():
    # Reads run with the service client: this API layer performs its own
    # authorisation, and a missing RLS policy would otherwise return an empty
    # list rather than an error. RLS still governs direct client access,
    # including the Realtime stream.
    supabase = get_admin_db()
    try:
        res = supabase.table("tournaments").select("*").order("created_at", desc=True).execute()
        # Hydrated here because the dashboard reads tournament.matches and
        # tournament.registrations straight off the list response.
        return _hydrate_tournaments(supabase, res.data or [])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{id}")
async def get_tournament(id: str):
    supabase = get_admin_db()
    try:
        t_res = supabase.table("tournaments").select("*").eq("id", id).execute()
        if not t_res.data:
            raise HTTPException(status_code=404, detail="Tournament not found")
        return _hydrate_tournaments(supabase, t_res.data)[0]
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
        res = admin_db.table("tournaments").insert(payload).execute()
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
        existing = admin_db.table("tournaments").select("*").eq("id", id).execute().data
        if not existing:
            raise HTTPException(status_code=404, detail="Tournament not found")
        before = existing[0]

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
        before = admin_db.table("tournaments").select("*").eq("id", id).execute().data
        if not before:
            raise HTTPException(status_code=404, detail="Tournament not found")

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
async def get_tournament_registrations(id: str):
    supabase = get_admin_db()
    try:
        # Ordered so the registrations list does not reshuffle between refreshes.
        res = supabase.table("registrations").select(
            "*, player:profiles(*), team:teams(*)"
        ).eq("tournament_id", id).order("registered_at").order("id").execute()
        return _hydrate_registrations(supabase, res.data or [])
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
                            "city": profile.get("city", "Pune")
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
async def generate_fixtures(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        # Load tournament details
        t_res = admin_db.table("tournaments").select("*").eq("id", id).execute()
        if not t_res.data:
            raise HTTPException(status_code=404, detail="Tournament not found")
        t = t_res.data[0]
        
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

        max_boards = t.get("rules", {}).get("maxBoardsPerMatch", 3)
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
                    return generate_knockout_bracket(id, pool, max_boards, id_prefix=prefix + "ko")
                if format_type in ("group_knockout", "league_knockout"):
                    return generate_group_knockout_fixtures(
                        id, pool, max_boards, group_count=group_count,
                        qualifiers_per_group=qualifiers_per_group, id_prefix=prefix + "gk")
                return generate_group_stage_fixtures(
                    id, pool, max_boards, group_count=group_count, id_prefix=prefix + "gs")

            if format_type == "round_robin":
                return generate_round_robin_fixtures(id, pool, max_boards, id_prefix=prefix + "rr")
            if format_type == "knockout":
                return generate_knockout_bracket(id, pool, max_boards, id_prefix=prefix + "ko")
            return generate_league_knockout_fixtures(id, pool, max_boards, id_prefix=prefix)

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

        # Clear existing matches first (cascade deletes boards)
        admin_db.table("matches").delete().eq("tournament_id", id).execute()

        # Insert new matches & boards
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
            
            inserted_match = admin_db.table("matches").insert(match_payload).execute()
            match_id = inserted_match.data[0]["id"]
            match["db_uuid"] = match_id  # Save reference for bracket linking

            # Create boards
            for board in match["boards"]:
                board_payload = {
                    "match_id": match_id,
                    "board_number": board["boardNumber"],
                    "status": "pending" if board["boardNumber"] > 1 else "in_progress",
                    "player1_score": 0,
                    "player2_score": 0
                }
                admin_db.table("boards").insert(board_payload).execute()

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

@router.post("/{id}/schedule")
async def generate_schedule(id: str, restMinutes: int = Query(10), admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        # Load tournament
        t_res = admin_db.table("tournaments").select("*").eq("id", id).execute()
        if not t_res.data:
            raise HTTPException(status_code=404, detail="Tournament not found")
        t = t_res.data[0]

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
