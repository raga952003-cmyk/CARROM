from fastapi import APIRouter, Depends, HTTPException, status
from app.database import get_db, get_admin_db
from app.models.player import PlayerSchema
from app.utils.security import verify_admin, get_optional_profile
from app.utils.serializers import serialize_player
from app.services.audit_service import record_audit
from typing import List, Dict, Any
import uuid
import secrets

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
        
        # Make sure role is set to player in app_metadata
        admin_db.auth.admin.update_user_by_id(
            auth_user.user.id,
            attributes={"app_metadata": {"role": "player"}}
        )

        # Update the profiles row (just in case fields weren't fully set by trigger)
        profile_update = {
            "name": data.name,
            "club": data.club or "Independent",
            "city": data.city or "Pune",
            "rating": data.rating or 1500,
            "phone": data.phone
        }
        
        res = admin_db.table("profiles").update(profile_update).eq("id", auth_user.user.id).execute()
        record_audit(
            admin_db, actor=admin, action="player.create",
            entity_type="player", entity_id=auth_user.user.id, new_state=res.data[0],
        )
        return serialize_player(res.data[0])
    except HTTPException:
        raise
    except Exception as e:
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
        return serialize_player(res.data[0])
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
