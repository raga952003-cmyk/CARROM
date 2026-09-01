"""
Tournament access requests (owner approval flow).

An admin who did not create a tournament asks its owner for access. The owner
sees the request and approves or rejects it; only then can the requester act on
that tournament.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.models.tournament import BaseCamelModel
from app.database import get_admin_db
from app.utils.security import verify_admin, get_user_profile
from app.utils.serializers import camelize, serialize_player
from app.services.access_control import (
    MANAGER, SCORER, load_tournament, owned_by, get_access_row,
    describe_access, ownership_enforced,
)
from app.services.notification_service import fan_out_notification
from app.services.audit_service import record_audit
from typing import Any, Dict, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/access", tags=["access"])


class AccessRequestSchema(BaseModel):
    role: str = MANAGER
    message: Optional[str] = None


class AccessDecisionSchema(BaseModel):
    note: Optional[str] = None
    role: Optional[str] = None


class AccessGrantSchema(BaseCamelModel):
    """
    Owner hands access to someone who has not asked for it.

    Camel-cased on purpose: the browser sends userId, and a plain BaseModel
    would silently drop it -- which read as "Provide either user_id or email"
    on a form where an admin had plainly been chosen. The other schemas here
    get away with BaseModel only because none of their fields have an
    underscore in them.
    """
    user_id: Optional[str] = None
    email: Optional[str] = None
    role: str = MANAGER
    note: Optional[str] = None


def _unavailable():
    raise HTTPException(
        status_code=503,
        detail=(
            "Tournament access control is not enabled on this database. "
            "Apply backend/db/migrations/003_ownership_and_access.sql."
        ),
    )


def _hydrate(admin_db, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach the requester profile and tournament name to each request."""
    if not rows:
        return []
    user_ids = {r["user_id"] for r in rows if r.get("user_id")}
    t_ids = {r["tournament_id"] for r in rows if r.get("tournament_id")}

    people = {}
    if user_ids:
        for p in (admin_db.table("profiles").select("id, name, email, club, role").in_(
                "id", list(user_ids)).execute().data or []):
            people[p["id"]] = p
    tournaments = {}
    if t_ids:
        for t in (admin_db.table("tournaments").select("id, name, owner_id").in_(
                "id", list(t_ids)).execute().data or []):
            tournaments[t["id"]] = t

    out = []
    for r in rows:
        item = camelize(r)
        item["requester"] = camelize(people.get(r.get("user_id")))
        item["tournament"] = camelize(tournaments.get(r.get("tournament_id")))
        out.append(item)
    return out


@router.post("/tournaments/{tournament_id}/request")
async def request_access(
    tournament_id: str,
    data: AccessRequestSchema,
    admin = Depends(verify_admin),
):
    """Ask the owner for access to a tournament you did not create."""
    if ownership_enforced() is False:
        _unavailable()
    if data.role not in (MANAGER, SCORER):
        raise HTTPException(status_code=422, detail="role must be 'manager' or 'scorer'.")

    admin_db = get_admin_db()
    tournament = load_tournament(admin_db, tournament_id)

    if str(tournament.get("owner_id") or "") == str(admin["id"]):
        raise HTTPException(status_code=409, detail="You already own this tournament.")

    try:
        existing = get_access_row(admin_db, tournament_id, admin["id"])
        if existing and existing.get("status") == "approved":
            raise HTTPException(status_code=409, detail="You already have access to this tournament.")
        if existing and existing.get("status") == "pending":
            raise HTTPException(status_code=409, detail="You already have a request awaiting a decision.")

        payload = {
            "tournament_id": tournament_id,
            "user_id": admin["id"],
            "access_role": data.role,
            "status": "pending",
            "message": data.message,
            "requested_at": datetime.utcnow().isoformat(),
            "decided_at": None,
            "decided_by": None,
            "decision_note": None,
        }
        # A previously rejected or revoked request is re-opened in place; the
        # table holds one row per person per tournament.
        row = admin_db.table("tournament_access").upsert(
            payload, on_conflict="tournament_id,user_id"
        ).execute().data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Access request failed for {tournament_id}: {str(e)}")
        _unavailable()

    if tournament.get("owner_id"):
        fan_out_notification(
            admin_db,
            title="Tournament access requested",
            message=(
                f"{admin['name']} has asked for {data.role} access to "
                f"'{tournament['name']}'." + (f" \"{data.message}\"" if data.message else "")
            ),
            type="access_requested",
            tournament_id=tournament_id,
            recipient_ids=[tournament["owner_id"]],
        )

    record_audit(
        admin_db, actor=admin, action="access.request",
        entity_type="tournament", entity_id=tournament_id,
        new_state={"role": data.role, "status": "pending"},
    )
    return _hydrate(admin_db, [row])[0]


@router.get("/tournaments/{tournament_id}/requests")
async def list_requests(
    tournament_id: str,
    status: Optional[str] = Query(None, pattern="^(pending|approved|rejected|revoked)$"),
    admin = Depends(verify_admin),
):
    """Requests for one tournament. Owner only."""
    admin_db = get_admin_db()
    owned_by(admin_db, tournament_id, admin)
    try:
        query = admin_db.table("tournament_access").select("*").eq("tournament_id", tournament_id)
        if status:
            query = query.eq("status", status)
        rows = query.order("requested_at", desc=True).execute().data or []
    except Exception:
        _unavailable()
    return _hydrate(admin_db, rows)


@router.get("/pending")
async def list_pending_for_owner(admin = Depends(verify_admin)):
    """Every request awaiting *my* decision, across all tournaments I own."""
    admin_db = get_admin_db()
    try:
        mine = admin_db.table("tournaments").select("id").eq(
            "owner_id", admin["id"]).execute().data or []
        if not mine:
            return []
        rows = admin_db.table("tournament_access").select("*").in_(
            "tournament_id", [t["id"] for t in mine]
        ).eq("status", "pending").order("requested_at", desc=True).execute().data or []
    except Exception:
        return []
    return _hydrate(admin_db, rows)


@router.get("/mine")
async def list_my_access(admin = Depends(verify_admin)):
    """Tournaments I own, plus my requests and their status."""
    admin_db = get_admin_db()
    try:
        owned = admin_db.table("tournaments").select("id, name, status").eq(
            "owner_id", admin["id"]).order("created_at", desc=True).execute().data or []
        rows = admin_db.table("tournament_access").select("*").eq(
            "user_id", admin["id"]).order("requested_at", desc=True).execute().data or []
    except Exception:
        return {"owned": [], "requests": [], "enforced": False}
    return {
        "owned": [camelize(t) for t in owned],
        "requests": _hydrate(admin_db, rows),
        "enforced": ownership_enforced() is not False,
    }


def _decide(request_id: str, decision: str, note: Optional[str],
            role: Optional[str], admin: Dict[str, Any]) -> Dict[str, Any]:
    admin_db = get_admin_db()
    try:
        rows = admin_db.table("tournament_access").select("*").eq("id", request_id).execute().data
    except Exception:
        _unavailable()
    if not rows:
        raise HTTPException(status_code=404, detail="Access request not found.")
    request_row = rows[0]

    tournament = owned_by(admin_db, request_row["tournament_id"], admin)

    patch = {
        "status": decision,
        "decided_at": datetime.utcnow().isoformat(),
        "decided_by": admin["id"],
        "decision_note": note,
    }
    if decision == "approved" and role in (MANAGER, SCORER):
        patch["access_role"] = role

    updated = admin_db.table("tournament_access").update(patch).eq(
        "id", request_id).execute().data[0]

    granted_role = updated.get("access_role") or MANAGER
    titles = {
        "approved": ("Tournament access granted", "access_granted",
                     f"You now have {granted_role} access to '{tournament['name']}'."),
        "rejected": ("Tournament access declined", "access_denied",
                     f"Your access request for '{tournament['name']}' was declined."),
        "revoked": ("Tournament access revoked", "access_revoked",
                    f"Your access to '{tournament['name']}' has been revoked."),
    }
    title, notif_type, message = titles[decision]
    if note:
        message += f" Note: {note}"

    fan_out_notification(
        admin_db, title=title, message=message, type=notif_type,
        tournament_id=tournament["id"], recipient_ids=[request_row["user_id"]],
    )
    record_audit(
        admin_db, actor=admin, action=f"access.{decision}",
        entity_type="tournament", entity_id=tournament["id"],
        previous_state={"status": request_row.get("status")},
        new_state={"status": decision, "role": granted_role, "user_id": request_row["user_id"]},
    )
    return _hydrate(admin_db, [updated])[0]


@router.post("/tournaments/{tournament_id}/grant")
async def grant_access(tournament_id: str, data: AccessGrantSchema,
                       admin = Depends(verify_admin)):
    """
    Give someone access without waiting for them to ask.

    Requests cover the case where a helper notices the tournament and wants in.
    This covers the other direction, which is the more common one on the day:
    the organiser knows who is scoring on table three and adds them, rather
    than telling them to go and request it so it can be approved.

    The row written is the same shape a request produces, already approved, so
    the two routes converge and revoking works identically for both.
    """
    if data.role not in (MANAGER, SCORER):
        raise HTTPException(status_code=422, detail="role must be 'manager' or 'scorer'.")

    admin_db = get_admin_db()
    # Only the owner decides who helps run their tournament.
    tournament = owned_by(admin_db, tournament_id, admin)

    if not data.user_id and not data.email:
        raise HTTPException(status_code=422, detail="Provide either user_id or email.")

    target = None
    try:
        if data.user_id:
            rows = admin_db.table("profiles").select("*").eq("id", data.user_id).execute().data
        else:
            rows = admin_db.table("profiles").select("*").eq(
                "email", (data.email or "").strip().lower()).execute().data
        target = rows[0] if rows else None
    except Exception as e:
        logger.error(f"Grant lookup failed for {tournament_id}: {str(e)}")
        _unavailable()

    if not target:
        raise HTTPException(
            status_code=404,
            detail="No account found for that person. They need to register before being given access.",
        )
    if target.get("role") != "admin":
        raise HTTPException(
            status_code=409,
            detail=(
                f"{target.get('name') or 'That account'} is a player, not an admin. "
                "Only admin accounts can be given scoring or management access."
            ),
        )
    if str(tournament.get("owner_id") or "") == str(target["id"]):
        raise HTTPException(status_code=409, detail="That person already owns this tournament.")

    try:
        row = admin_db.table("tournament_access").upsert({
            "tournament_id": tournament_id,
            "user_id": target["id"],
            "access_role": data.role,
            "status": "approved",
            "message": None,
            "requested_at": datetime.utcnow().isoformat(),
            "decided_at": datetime.utcnow().isoformat(),
            "decided_by": admin["id"],
            "decision_note": data.note,
        }, on_conflict="tournament_id,user_id").execute().data[0]
    except Exception as e:
        logger.error(f"Grant failed for {tournament_id}: {str(e)}")
        _unavailable()

    fan_out_notification(
        admin_db,
        title="Tournament access granted",
        message=(
            f"You now have {data.role} access to '{tournament['name']}'."
            + (f" Note: {data.note}" if data.note else "")
        ),
        type="access_granted",
        tournament_id=tournament_id,
        recipient_ids=[target["id"]],
    )
    record_audit(
        admin_db, actor=admin, action="access.grant",
        entity_type="tournament", entity_id=tournament_id,
        new_state={"role": data.role, "status": "approved", "user_id": target["id"]},
    )
    return _hydrate(admin_db, [row])[0]


@router.get("/admins")
async def list_admins(admin = Depends(verify_admin)):
    """Admin accounts that can be granted access, for the owner's picker."""
    admin_db = get_admin_db()
    try:
        rows = admin_db.table("profiles").select("id,name,email").eq(
            "role", "admin").order("name").execute().data or []
    except Exception:
        return []
    return [camelize(r) for r in rows if r["id"] != admin["id"]]


@router.post("/requests/{request_id}/approve")
async def approve_request(request_id: str, data: AccessDecisionSchema,
                          admin = Depends(verify_admin)):
    return _decide(request_id, "approved", data.note, data.role, admin)


@router.post("/requests/{request_id}/reject")
async def reject_request(request_id: str, data: AccessDecisionSchema,
                         admin = Depends(verify_admin)):
    return _decide(request_id, "rejected", data.note, None, admin)


@router.post("/requests/{request_id}/revoke")
async def revoke_request(request_id: str, data: AccessDecisionSchema,
                         admin = Depends(verify_admin)):
    """Withdraw access that was previously granted."""
    return _decide(request_id, "revoked", data.note, None, admin)


@router.get("/tournaments/{tournament_id}/me")
async def my_access_for(tournament_id: str, profile = Depends(get_user_profile)):
    """What the caller may do here — drives which controls the UI offers."""
    admin_db = get_admin_db()
    tournament = load_tournament(admin_db, tournament_id)
    return describe_access(admin_db, tournament, profile)
