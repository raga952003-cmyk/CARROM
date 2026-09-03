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

    Contact details are returned only to admins. This endpoint is reachable
    without a session (the spectator views rely on it), and it previously
    returned whole profile rows -- publishing every participant's phone number
    and email address.
    """
    supabase = get_admin_db()
    is_admin = bool(viewer and viewer.get("role") == "admin")
    columns = "*" if is_admin else "id, name, avatar, club, city, rating, role, created_at"
    try:
        res = supabase.table("profiles").select(columns).eq(
            "role", "player").order("name").execute()
        return [serialize_player(p, include_contact=is_admin) for p in (res.data or [])]
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

@router.put("/{id}")
async def update_player(id: str, data: PlayerSchema, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
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

@router.delete("/{id}")
async def delete_player(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
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
