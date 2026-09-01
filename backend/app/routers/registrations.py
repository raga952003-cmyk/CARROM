from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db, get_admin_db
from app.utils.security import verify_admin, get_optional_profile
from app.services.access_control import require_tournament_access
from app.utils.serializers import serialize_registration
from app.services.notification_service import fan_out_notification
from app.services.audit_service import record_audit
from typing import List, Dict, Any, Optional

router = APIRouter(prefix="/registrations", tags=["registrations"])


def _recipients_for(registration: Dict[str, Any], admin_db) -> List[str]:
    """The player, or both members of the team, attached to this registration."""
    if registration.get("player_id"):
        return [registration["player_id"]]

    team_id = registration.get("team_id")
    if not team_id:
        return []

    team = admin_db.table("teams").select("player1_id, player2_id").eq(
        "id", team_id
    ).execute().data
    if not team:
        return []
    return [pid for pid in (team[0].get("player1_id"), team[0].get("player2_id")) if pid]


def _set_status(id: str, status: str, admin_db, actor=None):
    existing = admin_db.table("registrations").select("*").eq("id", id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Registration not found.")
    before = existing.data[0]

    res = admin_db.table("registrations").update({"status": status}).eq("id", id).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Failed to update registration status.")

    record_audit(
        admin_db, actor=actor, action=f"registration.{status}",
        entity_type="registration", entity_id=id,
        previous_state=before, new_state=res.data[0],
    )
    return res.data[0]


def _authorise_registration(admin_db, registration_id: str, admin):
    """Deciding a registration belongs to whoever runs that tournament."""
    rows = admin_db.table("registrations").select("tournament_id").eq(
        "id", registration_id).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Registration not found.")
    require_tournament_access(admin_db, rows[0]["tournament_id"], admin)


@router.post("/{id}/approve")
async def approve_registration(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        _authorise_registration(admin_db, id, admin)

        registration = _set_status(id, "approved", admin_db, actor=admin)

        tournament = admin_db.table("tournaments").select("name").eq(
            "id", registration["tournament_id"]
        ).execute().data
        tournament_name = tournament[0]["name"] if tournament else "the tournament"

        fan_out_notification(
            admin_db,
            title="Registration Approved",
            message=f"Your entry for '{tournament_name}' has been approved. You are now in the draw.",
            type="registration_confirmed",
            tournament_id=registration["tournament_id"],
            recipient_ids=_recipients_for(registration, admin_db),
        )
        return serialize_registration(registration, include_contact=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{id}/reject")
async def reject_registration(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        _authorise_registration(admin_db, id, admin)

        registration = _set_status(id, "rejected", admin_db, actor=admin)

        tournament = admin_db.table("tournaments").select("name").eq(
            "id", registration["tournament_id"]
        ).execute().data
        tournament_name = tournament[0]["name"] if tournament else "the tournament"

        fan_out_notification(
            admin_db,
            title="Registration Not Accepted",
            message=f"Your entry for '{tournament_name}' was not accepted. Please contact the organisers for details.",
            type="registration_confirmed",
            tournament_id=registration["tournament_id"],
            recipient_ids=_recipients_for(registration, admin_db),
        )
        return serialize_registration(registration, include_contact=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{id}")
async def get_registration(id: str, viewer = Depends(get_optional_profile)):
    supabase = get_admin_db()
    try:
        res = supabase.table("registrations").select(
            "*, player:profiles(*), team:teams(*)"
        ).eq("id", id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Registration not found.")
        is_admin = bool(viewer and viewer.get("role") == "admin")
        return serialize_registration(res.data[0], is_admin)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
