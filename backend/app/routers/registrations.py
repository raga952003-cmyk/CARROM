from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db, get_admin_db
from app.utils.security import verify_admin, get_optional_profile
from app.services.access_control import require_tournament_access
from app.utils.serializers import serialize_registration
from app.services.notification_service import fan_out_notification
from app.services.audit_service import record_audit
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from uuid import uuid4
from datetime import datetime, timezone
import re

router = APIRouter(prefix="/registrations", tags=["registrations"])


class ManualPayment(BaseModel):
    method: str = Field(pattern="^(cash|upi|bank_transfer)$")
    reference: str = Field(min_length=3, max_length=120)


class FeeWaiver(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


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
    if status == "approved" and before.get("payment_status") not in ("paid", "waived"):
        fee = before.get("fee_paise")
        if fee is None:
            tournament = admin_db.table("tournaments").select("entry_fee").eq(
                "id", before["tournament_id"]).execute().data
            fee = round(float(tournament[0].get("entry_fee") or 0) * 100) if tournament else 0
        if int(fee) > 0:
            raise HTTPException(status_code=409, detail="Record payment or waive the fee before approving this entry.")

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


def _require_settleable_entry(db, registration: Dict[str, Any]) -> None:
    """A payment or waiver must never reinstate a rejected or finished entry."""
    if registration.get("status") == "rejected":
        raise HTTPException(status_code=409, detail="A rejected entry cannot be approved by recording payment.")
    tournaments = db.table("tournaments").select("status").eq(
        "id", registration["tournament_id"]).execute().data or []
    if not tournaments or tournaments[0].get("status") in ("cancelled", "completed"):
        raise HTTPException(status_code=409, detail="This tournament no longer accepts entry payments.")


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


@router.post("/{id}/manual-payment")
async def record_manual_payment(id: str, body: ManualPayment, admin=Depends(verify_admin)):
    db = get_admin_db()
    _authorise_registration(db, id, admin)
    rows = db.table("registrations").select("*").eq("id", id).execute().data
    registration = rows[0]
    _require_settleable_entry(db, registration)
    if registration.get("payment_status") != "pending":
        raise HTTPException(status_code=409, detail="This entry is already settled.")
    fee = registration.get("fee_paise")
    if fee is None:
        tournament = db.table("tournaments").select("entry_fee").eq("id", registration["tournament_id"]).execute().data
        fee = round(float(tournament[0].get("entry_fee") or 0) * 100) if tournament else 0
    fee = int(fee)
    if fee <= 0:
        raise HTTPException(status_code=409, detail="This entry has no fee to collect.")
    reference = body.reference.strip()
    if body.method in ("upi", "bank_transfer"):
        reference = re.sub(r"[^A-Za-z0-9]", "", reference).upper()
        if not 6 <= len(reference) <= 80:
            raise HTTPException(status_code=422, detail="Enter a 6 to 80 character bank or UPI transaction reference.")
        # A proof remains reserved after rejection: reusing the same transaction
        # on a different entry must not become a second payment.
        claimed = db.table("payment_proofs").select("id").eq(
            "transaction_reference", reference).limit(1).execute().data or []
        if claimed:
            raise HTTPException(status_code=409, detail="This transaction reference was already submitted as payment proof.")
    existing = db.table("payments").select("id, razorpay_order_id").eq("registration_id", id).eq("status", "paid").execute().data
    if existing:
        # A previous attempt can have committed the ledger row before its
        # registration update failed. Repair that state without a new charge.
        repaired = db.table("registrations").update({
            "payment_status": "paid", "status": "approved",
        }).eq(
            "id", id).eq("payment_status", "pending").execute().data
        if not repaired:
            raise HTTPException(status_code=409, detail="A payment is already recorded for this entry. Reload it.")
        record_audit(db, actor=admin, action="payment.registration_reconciled",
                     entity_type="registration", entity_id=id, previous_state=registration,
                     new_state=repaired[0])
        return serialize_registration(repaired[0], include_contact=True)
    payment = db.table("payments").insert({
        "registration_id": id, "tournament_id": registration["tournament_id"],
        "razorpay_order_id": f"manual-{uuid4()}", "amount_paise": fee,
        "status": "paid", "method": body.method, "paid_at": datetime.now(timezone.utc).isoformat(),
        "notes": {"reference": reference, "recorded_by": admin.get("id")},
    }).execute().data[0]
    updated = db.table("registrations").update({
        "payment_status": "paid", "status": "approved",
    }).eq("id", id).eq("payment_status", "pending").execute().data
    if not updated:
        raise HTTPException(status_code=503, detail="Payment was recorded, but the entry needs reconciliation before approval.")
    record_audit(db, actor=admin, action="payment.manual_recorded", entity_type="payment",
                 entity_id=payment["id"], new_state=payment)
    record_audit(db, actor=admin, action="registration.auto_approved_after_payment",
                 entity_type="registration", entity_id=id,
                 previous_state=registration, new_state=updated[0])
    return serialize_registration(updated[0], include_contact=True)


@router.post("/{id}/waive-fee")
async def waive_fee(id: str, body: FeeWaiver, admin=Depends(verify_admin)):
    db = get_admin_db()
    _authorise_registration(db, id, admin)
    before = db.table("registrations").select("*").eq("id", id).execute().data[0]
    _require_settleable_entry(db, before)
    if before.get("payment_status") != "pending":
        raise HTTPException(status_code=409, detail="This entry is already settled.")
    paid = db.table("payments").select("id").eq("registration_id", id).eq("status", "paid").execute().data
    if paid:
        raise HTTPException(status_code=409, detail="A payment is already recorded for this entry.")
    updated = db.table("registrations").update({
        "payment_status": "waived", "status": "approved",
    }).eq("id", id).eq("payment_status", "pending").execute().data
    if not updated:
        raise HTTPException(status_code=409, detail="The entry changed; reload and try again.")
    record_audit(db, actor=admin, action="payment.fee_waived", entity_type="registration",
                 entity_id=id, previous_state=before, new_state={**updated[0], "waiver_reason": body.reason})
    return serialize_registration(updated[0], include_contact=True)


@router.post("/{id}/reject")
async def reject_registration(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        _authorise_registration(admin_db, id, admin)

        registration = _set_status(id, "rejected", admin_db, actor=admin)
        if registration.get("payment_status") == "paid":
            paid = admin_db.table("payments").select("id, razorpay_payment_id, amount_paise").eq(
                "registration_id", id).eq("status", "paid").execute().data or []
            record_audit(
                admin_db, actor=admin, action="payment.needs_refund_decision",
                entity_type="registration", entity_id=id,
                new_state={"status": "rejected", "payment_status": "paid", "payments": paid},
            )

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
        is_admin = False
        if viewer and viewer.get("role") == "admin":
            try:
                require_tournament_access(supabase, res.data[0]["tournament_id"], viewer)
                is_admin = True
            except HTTPException:
                pass
        return serialize_registration(res.data[0], is_admin)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
