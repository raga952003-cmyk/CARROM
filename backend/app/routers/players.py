from app.services.entry_integrity import anywhere, refusal_detail
from fastapi import APIRouter, Depends, HTTPException, status
from app.database import get_db, get_admin_db
from app.models.player import PlayerSchema
from app.utils.security import verify_admin, get_optional_profile
from app.utils.serializers import serialize_player
from app.services.audit_service import record_audit
from typing import List, Dict, Any
import logging
import uuid
import secrets

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/players", tags=["players"])

@router.get("")
async def get_players(viewer = Depends(get_optional_profile)):
    """
    Player directory.

    The directory is public. Contact details belong only in an authorized
    tournament's registration view.
    """
    supabase = get_admin_db()
    columns = "id, name, avatar, club, city, rating, role, created_at"
    try:
        res = supabase.table("profiles").select(columns).eq(
            "role", "player").order("name").execute()
        return [serialize_player(p, include_contact=False) for p in (res.data or [])]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("")
async def create_player(data: PlayerSchema, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        # Create an auth account for the player first
        email = data.email or f"player_{uuid.uuid4().hex[:8]}@carromarena.com"

        # Admin-created players never sign in with this password; they use the
        # Supabase password-reset flow. A random one avoids a guessable
        # credential on every generated account.
        auth_user = admin_db.auth.admin.create_user({
            "email": email,
            "password": secrets.token_urlsafe(32),
            "email_confirm": True,
            "user_metadata": {
                "name": data.name,
                "role": "player",
                "club": data.club,
                "city": data.city,
                "rating": data.rating
            }
        })
        
        if not auth_user or not auth_user.user:
            raise HTTPException(status_code=400, detail="Failed to create auth credentials for player.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # From here the auth user exists, so every failure has to take it back out
    # again. Without this the account survived a half-finished creation with no
    # profiles row: invisible in the players list, unusable to sign in with,
    # and holding its email address hostage against a retry. Repeated attempts
    # built up a whole shadow roster that way. Sign-up already had this
    # rollback; player creation did not.
    user_id = auth_user.user.id
    try:
        # Make sure role is set to player in app_metadata
        admin_db.auth.admin.update_user_by_id(
            user_id,
            attributes={"app_metadata": {"role": "player"}}
        )

        # UPSERT, not UPDATE. The profiles row is normally created by the
        # handle_new_user trigger (db/triggers_and_security.sql), but that file
        # is applied by hand and is not part of the numbered migrations. Where
        # it is missing, an UPDATE matched nothing, res.data[0] raised
        # IndexError, and the caller was told "list index out of range".
        profile_row = {
            "id": user_id,
            "email": email,
            "role": "player",
            "name": data.name,
            "club": data.club or "Independent",
            "city": data.city,
            "rating": data.rating or 1500,
            "phone": data.phone,
        }

        res = admin_db.table("profiles").upsert(profile_row).execute()
        if not res.data:
            raise HTTPException(
                status_code=500,
                detail="The player's profile could not be created. Nothing was saved.",
            )
        record_audit(
            admin_db, actor=admin, action="player.create",
            entity_type="player", entity_id=user_id, new_state=res.data[0],
        )
        return serialize_player(res.data[0], include_contact=True)
    except Exception as e:
        try:
            admin_db.auth.admin.delete_user(user_id)
        except Exception as cleanup_error:
            logger.error(
                "Could not roll back the auth user %s after a failed player "
                "creation; it is now an orphan: %s", user_id, str(cleanup_error)
            )
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(e))

def _assert_target_is_a_player(admin_db, player_id: str) -> Dict[str, Any]:
    """
    This router is the PLAYER directory. It does not touch admin accounts.

    Neither write here checked the target's ROLE, only that the caller was an
    admin -- so one organiser could delete another's account outright. GET
    /players filters on role='player' (so an admin is not even listed), but
    DELETE and PUT took any id, and the id is not a secret: every tournament
    payload carries its owner's as `ownerId`.

    Deleting it cascades. profiles.id references auth.users ON DELETE CASCADE
    (schema.sql:6), and tournaments.owner_id references profiles ON DELETE SET
    NULL (003_ownership_and_access.sql:27) -- so the victim's tournaments
    become "unowned", and access_control treats unowned as manageable by ANY
    admin. Probed end to end: an outsider refused with 403 ("Only the
    tournament owner can do this") deleted the owner's account, and then
    edited and deleted the tournament, both 200. The ownership boundary this
    codebase enforces by default falls to one call.
    """
    rows = admin_db.table("profiles").select("id, name, role").eq(
        "id", player_id).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Player profile not found.")
    target = rows[0]
    if (target.get("role") or "player") != "player":
        raise HTTPException(
            status_code=403,
            detail=(
                "This is the player directory and that account is an "
                f"{target.get('role')}. Administrator accounts are not managed here."
            ),
        )
    return target


@router.put("/{id}")
async def update_player(id: str, data: PlayerSchema, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        _assert_target_is_a_player(admin_db, id)

        profile_update = {}
        if data.name is not None: profile_update["name"] = data.name
        if data.club is not None: profile_update["club"] = data.club
        if data.city is not None: profile_update["city"] = data.city
        if data.rating is not None: profile_update["rating"] = data.rating
        if data.phone is not None: profile_update["phone"] = data.phone
        
        before = admin_db.table("profiles").select("*").eq("id", id).execute().data
        res = admin_db.table("profiles").update(profile_update).eq("id", id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Player profile not found.")
        record_audit(
            admin_db, actor=admin, action="player.update",
            entity_type="player", entity_id=id,
            previous_state=before[0] if before else None, new_state=res.data[0],
        )
        return serialize_player(res.data[0], include_contact=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

def _refuse_delete_with_entries(admin_db, player_id: str) -> None:
    """
    Deleting a player deletes their results, and their opponents' results.

    This route calls auth.admin.delete_user, and the cascade runs all the way
    down: profiles.id references auth.users ON DELETE CASCADE (schema.sql:6)
    and registrations.player_id references profiles ON DELETE CASCADE
    (schema.sql:70). The registration goes, and with it the entrant leaves the
    pool the points table is built from -- so every match they played drops
    out of their OPPONENTS' records too.

    Measured on the September tournament's shape, 20 entrants with the league
    complete: deleting one entrant rewrote all 19 other rows, and deleting the
    4th-placed entrant moved the 9th-placed entrant into the top 8. The only
    check on this route was the target's ROLE. Nothing asked whether they were
    playing in anything.

    Rejecting an entry already refuses on the same grounds; this is the other
    button in the same admin screen, and it was the more destructive one.
    """
    detail = refusal_detail(anywhere(admin_db, player_id), "deleting them")
    if detail:
        raise HTTPException(status_code=409, detail=detail)

    # Doubles: the match names the TEAM, not the player inside it, so the
    # pass above cannot see it. Their registrations give the team ids.
    regs = admin_db.table("registrations").select(
        "team_id").eq("player_id", player_id).execute().data or []
    for team_id in {r.get("team_id") for r in regs if r.get("team_id")}:
        detail = refusal_detail(anywhere(admin_db, team_id), "deleting them")
        if detail:
            raise HTTPException(status_code=409, detail=detail)


@router.delete("/{id}")
async def delete_player(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        _assert_target_is_a_player(admin_db, id)
        _refuse_delete_with_entries(admin_db, id)

        # Delete user from Supabase Auth, which cascades to public.profiles
        before = admin_db.table("profiles").select("*").eq("id", id).execute().data
        admin_db.auth.admin.delete_user(id)
        record_audit(
            admin_db, actor=admin, action="player.delete",
            entity_type="player", entity_id=id,
            previous_state=before[0] if before else None,
        )
        return {"status": "success", "message": "Player deleted successfully."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
