from app.services.entry_integrity import entanglement, refusal_detail
from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db, get_admin_db
from app.utils.security import verify_admin, get_optional_profile
from app.services.access_control import require_tournament_access
from app.utils.serializers import serialize_registration
from app.services.notification_service import fan_out_notification
from app.services.audit_service import record_audit
from app.config import settings
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
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


def _require_no_pending_payment_proof(db, registration_id: str) -> None:
    """A claimed GPay transfer must be reviewed before desk settlement or waiver."""
    proof = db.table("payment_proofs").select("id").eq(
        "registration_id", registration_id).eq("status", "pending").limit(1).execute().data or []
    if proof:
        raise HTTPException(
            status_code=409,
            detail="A GPay proof is awaiting review. Verify or reject it before recording another payment or waiving the fee.",
        )


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
    reference = body.reference.strip()
    if body.method in ("upi", "bank_transfer"):
        reference = re.sub(r"[^A-Za-z0-9]", "", reference).upper()
        if not 6 <= len(reference) <= 80:
            raise HTTPException(status_code=422, detail="Enter a 6 to 80 character bank or UPI transaction reference.")
    # The RPC locks the entry and writes payment, approval and audit together.
    # It also checks existing payments, proof claims and references inside that
    # same transaction, including if two organisers submit concurrently.
    try:
        result = db.rpc("record_manual_entry_payment", {
            "p_registration_id": id,
            "p_actor_id": admin["id"],
            "p_method": body.method,
            "p_reference": reference,
            "p_allow_any_admin": not settings.ENFORCE_TOURNAMENT_OWNERSHIP,
        }).execute().data
    except Exception as exc:
        message = str(exc)
        code = str(getattr(exc, "code", "") or "")
        if code == "PGRST202" or "Could not find the function" in message:
            raise HTTPException(status_code=503, detail="Atomic desk payment is not installed. Apply migration 023 before collecting entry fees.") from exc
        if code in ("P0001", "23505") or "duplicate" in message.lower():
            raise HTTPException(status_code=409, detail=message) from exc
        raise HTTPException(status_code=503, detail="Could not record payment safely. Reload the entry and retry.") from exc
    if isinstance(result, list):
        result = result[0] if result else None
    if not isinstance(result, dict) or not result.get("registration"):
        raise HTTPException(status_code=503, detail="Payment result was incomplete. Reload the entry before retrying.")
    return serialize_registration(result["registration"], include_contact=True)


@router.post("/{id}/waive-fee")
async def waive_fee(id: str, body: FeeWaiver, admin=Depends(verify_admin)):
    db = get_admin_db()
    _authorise_registration(db, id, admin)
    before = db.table("registrations").select("*").eq("id", id).execute().data[0]
    _require_settleable_entry(db, before)
    if before.get("payment_status") != "pending":
        raise HTTPException(status_code=409, detail="This entry is already settled.")
    _require_no_pending_payment_proof(db, id)
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


def _refuse_reject_with_confirmed_results(admin_db, registration_id: str) -> None:
    """
    An entrant with results played cannot simply be un-entered.

    The points table is built from registrations that are still 'approved'
    (standings._participants_for), and calculate_points_table drops any match
    whose two sides are not both in that pool. So rejecting somebody mid-event
    does not just remove them -- it removes every match they played, and takes
    their OPPONENTS' results with it.

    Probed: a three-player round robin, all three matches confirmed. Rejecting
    Cara returned 200 with no warning, and Bob -- an uninvolved third party --
    went from 2 played / 1 won / 2 points to 1 / 0 / 0. He lost a match he had
    actually won, which is enough to move him across a qualifying cut.

    Refused rather than repaired here, mirroring the fixture routes, which
    already refuse a destructive operation when result_confirmed holds. It
    used to look ONLY at result_confirmed, which let a walkover and a fixture
    still to be played through; services/entry_integrity.py now answers both,
    and DELETE /api/players/{id} asks the same question. The
    deeper fix is for the table to stop deriving its pool from live
    registration status, so an entrant can be withdrawn without rewriting
    anybody else's record; that is a larger change than this guard.
    """
    # Loaded here rather than taken from the caller: _authorise_registration
    # returns None, so passing "the row it already read" silently handed this
    # an empty dict and the guard returned without checking anything.
    rows = admin_db.table("registrations").select(
        "tournament_id, player_id, team_id").eq("id", registration_id).execute().data or []
    if not rows:
        return
    registration = rows[0]

    tournament_id = registration.get("tournament_id")
    participant = registration.get("player_id") or registration.get("team_id")
    if not tournament_id or not participant:
        return

    detail = refusal_detail(
        entanglement(admin_db, tournament_id, participant), "rejecting them")
    if detail:
        raise HTTPException(status_code=409, detail=detail)


@router.post("/{id}/reject")
async def reject_registration(id: str, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        _authorise_registration(admin_db, id, admin)
        _refuse_reject_with_confirmed_results(admin_db, id)

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
