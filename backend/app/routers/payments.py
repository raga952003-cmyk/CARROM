"""
Entry fees, collected through Razorpay.

The shape of the flow, and why it is this shape:

    1. The player submits the registration form. A registration row is created
       exactly as before, `status='pending'`, `payment_status='pending'`, and
       the fee in force at that moment is snapshotted onto it.
    2. The browser asks this router to open an order. The AMOUNT IS READ FROM
       THE REGISTRATION, never from the request -- the client is told what it
       owes, it does not get to say.
    3. Razorpay Checkout takes the money and hands the browser back three
       values, all of which are attacker-controlled.
    4. /verify checks the HMAC over those values, then asks Razorpay directly
       what the payment actually was, and only then marks the entry paid and
       confirmed.
    5. /webhook does the same thing without the browser, because step 3 does
       not happen if the player closes the tab, and because a browser callback
       that never arrives must not cost somebody their entry.

Steps 4 and 5 converge on `_settle_payment`, which is idempotent. That matters
more than it looks: Razorpay retries webhooks, the callback and the webhook
routinely both arrive for the same payment, and a player who double-taps gets
two verify calls. All of those must confirm one entry, once.

What a verified signature does NOT tell you is how much was paid -- it signs
the order and payment ids and nothing else. So `_settle_payment` fetches the
payment from Razorpay and compares the amount against what was recorded at
order time. Without that check, a payment for a different, cheaper order of
the same player's could be presented against this one.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app.database import get_admin_db
from app.models.payment import PaymentVerifySchema
from app.services import razorpay_client
from app.services.access_control import require_tournament_access
from app.services.audit_service import record_audit
from app.services.notification_service import fan_out_notification
from app.services.razorpay_client import RazorpayError
from app.utils.security import get_user_profile
from app.utils.serializers import serialize_payment, serialize_registration

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/payments", tags=["payments"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_registration(admin_db, registration_id: str) -> Dict[str, Any]:
    rows = admin_db.table("registrations").select("*").eq("id", registration_id).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Registration not found.")
    return rows[0]


def _participant_ids(admin_db, registration: Dict[str, Any]) -> List[str]:
    """Everyone this entry belongs to: the player, or both team members."""
    if registration.get("player_id"):
        return [registration["player_id"]]

    team_id = registration.get("team_id")
    if not team_id:
        return []

    rows = admin_db.table("teams").select("player1_id, player2_id").eq(
        "id", team_id).execute().data
    if not rows:
        return []
    return [pid for pid in (rows[0].get("player1_id"), rows[0].get("player2_id")) if pid]


def _authorise_payer(admin_db, registration: Dict[str, Any], profile: Dict[str, Any]) -> None:
    """
    Only the people this entry is for may pay it -- or an organiser of the
    tournament, who may take a payment on their behalf at the desk.

    A doubles entry is payable by EITHER partner, deliberately: the team enters
    once and one of them settles it, and forcing that to be the captain would
    strand a team whose captain is not the one holding the phone.
    """
    if str(profile.get("id")) in [str(pid) for pid in _participant_ids(admin_db, registration)]:
        return

    if profile.get("role") == "admin":
        # Raises 403 unless this admin actually runs this tournament.
        require_tournament_access(
            admin_db, registration["tournament_id"], profile, "registration.payment"
        )
        return

    raise HTTPException(
        status_code=403,
        detail="This entry is not yours to pay for.",
    )


def _fee_paise_for(admin_db, registration: Dict[str, Any]) -> int:
    """
    What this entry owes, in paise.

    Prefers the snapshot taken when the entry was made. An organiser is free to
    change `entry_fee` while registration is open -- for an early-bird rate, or
    because it was typed wrong -- and a player who entered under the old price
    owes the old price. Falls back to the tournament's current fee for entries
    made before migration 015 added the column.
    """
    snapshot = registration.get("fee_paise")
    if snapshot is not None:
        return int(snapshot)

    rows = admin_db.table("tournaments").select("entry_fee").eq(
        "id", registration["tournament_id"]).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Tournament not found.")
    return razorpay_client.rupees_to_paise(rows[0].get("entry_fee"))


def _tournament_name(admin_db, tournament_id: str) -> str:
    rows = admin_db.table("tournaments").select("name").eq(
        "id", tournament_id).execute().data
    return rows[0]["name"] if rows else "the tournament"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@router.get("/config")
async def payment_config(profile=Depends(get_user_profile)):
    """
    Whether this server can take payments, and the key the browser needs.

    `keyId` is public by design -- Razorpay Checkout takes it in the browser --
    but it is served from here rather than baked into the frontend build so
    that moving from test to live is a server environment change and not a
    rebuild. It also means the bundle can never carry a live key by accident.
    """
    configured = razorpay_client.razorpay_configured()
    return {
        "enabled": configured,
        # Withheld unless the pair is complete: a key_id with no secret behind
        # it would open a checkout this server could never verify.
        "keyId": _key_id() if configured else "",
        "liveMode": razorpay_client.is_live_mode(),
    }


# ---------------------------------------------------------------------------
# Opening an order
# ---------------------------------------------------------------------------

@router.post("/registrations/{registration_id}/order")
async def create_payment_order(registration_id: str, profile=Depends(get_user_profile)):
    """
    Open (or re-open) the Razorpay order for one registration.

    Re-entrant on purpose. A player who closes checkout and taps Pay again, or
    reloads the page mid-payment, comes back through here; returning the
    existing unpaid order rather than minting a new one keeps the dashboard
    readable and means one entry does not accumulate a dozen abandoned orders.
    """
    admin_db = get_admin_db()

    if not razorpay_client.razorpay_configured():
        raise HTTPException(
            status_code=503,
            detail="Online payment is not configured for this event. "
                   "Please contact the organisers to pay your entry fee.",
        )

    registration = _load_registration(admin_db, registration_id)
    _authorise_payer(admin_db, registration, profile)

    if registration.get("payment_status") == "paid":
        raise HTTPException(status_code=409, detail="This entry is already paid.")
    if registration.get("payment_status") == "waived":
        raise HTTPException(status_code=409, detail="This entry's fee has been waived.")
    if registration.get("status") == "rejected":
        raise HTTPException(
            status_code=409,
            detail="This entry was not accepted, so there is nothing to pay.",
        )

    amount_paise = _fee_paise_for(admin_db, registration)
    if amount_paise <= 0:
        raise HTTPException(
            status_code=400,
            detail="This tournament has no entry fee.",
        )

    # An order already open for the right amount is reused. The amount is part
    # of the condition because a fee correction between attempts must not let
    # the player settle at the stale price.
    existing = admin_db.table("payments").select("*").eq(
        "registration_id", registration_id).eq("status", "created").execute().data
    for row in existing or []:
        if int(row.get("amount_paise") or 0) == amount_paise:
            return {
                "payment": serialize_payment(row),
                "orderId": row["razorpay_order_id"],
                "amount": amount_paise,
                "currency": row.get("currency") or "INR",
                "keyId": _key_id(),
                "prefill": _prefill(admin_db, profile),
                "tournamentName": _tournament_name(admin_db, registration["tournament_id"]),
            }

    try:
        order = await razorpay_client.create_order(
            amount_paise=amount_paise,
            receipt=registration_id,
            notes={
                "registration_id": registration_id,
                "tournament_id": registration["tournament_id"],
                "type": registration.get("type") or "singles",
            },
        )
    except RazorpayError as e:
        # 502: this server is fine, the payment provider refused or was
        # unreachable. Distinguishable by the caller from a 400 they caused.
        raise HTTPException(status_code=502, detail=str(e))

    payment_row = {
        "registration_id": registration_id,
        "tournament_id": registration["tournament_id"],
        "razorpay_order_id": order["id"],
        "amount_paise": amount_paise,
        "currency": order.get("currency") or "INR",
        "status": "created",
        "signature_verified": False,
    }
    inserted = admin_db.table("payments").insert(payment_row).execute()
    stored = inserted.data[0] if inserted.data else payment_row

    record_audit(
        admin_db, actor=profile, action="payment.order_created",
        entity_type="payment", entity_id=str(stored.get("id") or order["id"]),
        new_state={"order_id": order["id"], "amount_paise": amount_paise,
                   "registration_id": registration_id},
    )

    return {
        "payment": serialize_payment(stored),
        "orderId": order["id"],
        "amount": amount_paise,
        "currency": order.get("currency") or "INR",
        "keyId": _key_id(),
        "prefill": _prefill(admin_db, profile),
        "tournamentName": _tournament_name(admin_db, registration["tournament_id"]),
    }


def _key_id() -> str:
    from app.config import settings
    return settings.RAZORPAY_KEY_ID


def _prefill(admin_db, profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    What Checkout should fill in for the payer.

    Convenience only -- Razorpay lets the payer edit all of it -- so nothing
    downstream may treat these as identifying the payment.
    """
    return {
        "name": profile.get("name") or "",
        "email": profile.get("email") or "",
        "contact": profile.get("phone") or "",
    }


# ---------------------------------------------------------------------------
# Settling
# ---------------------------------------------------------------------------

async def _settle_payment(admin_db, payment: Dict[str, Any], razorpay_payment_id: str,
                          via: str, actor: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Confirm a payment and the entry behind it. Safe to call more than once.

    Returns the payment row as it now stands.

    The ordering here is deliberate: verify, then confirm with Razorpay, then
    write. Nothing is marked paid on the strength of the request alone, and the
    registration is only touched once the payment row is settled -- so a crash
    between the two leaves an entry that is provably paid and merely
    unconfirmed, which an organiser can fix, rather than a confirmed entry with
    no money behind it, which they cannot.
    """
    # Already settled: return what we have. This is the idempotency guarantee
    # the webhook retries and the double-tapped browser both rely on.
    if payment.get("status") == "paid":
        return payment

    # What Razorpay says this payment actually is. The signature proved who
    # sent the message; this proves what the message is about.
    try:
        remote = await razorpay_client.fetch_payment(razorpay_payment_id)
    except RazorpayError as e:
        raise HTTPException(status_code=502, detail=str(e))

    expected_paise = int(payment.get("amount_paise") or 0)
    actual_paise = int(remote.get("amount") or 0)
    remote_order_id = remote.get("order_id")
    remote_status = remote.get("status")

    if remote_order_id != payment.get("razorpay_order_id"):
        # A real payment, but for a different order. Presenting one order's
        # payment against another is exactly the substitution the amount check
        # exists to stop, so it is refused loudly and recorded.
        logger.warning(
            "Payment %s belongs to order %s, not %s",
            razorpay_payment_id, remote_order_id, payment.get("razorpay_order_id"),
        )
        raise HTTPException(
            status_code=400,
            detail="This payment does not belong to this entry.",
        )

    if actual_paise != expected_paise:
        logger.warning(
            "Payment %s paid %s paise against an expected %s",
            razorpay_payment_id, actual_paise, expected_paise,
        )
        raise HTTPException(
            status_code=400,
            detail="The amount paid does not match the entry fee. "
                   "Please contact the organisers.",
        )

    if remote_status not in ("captured", "authorized"):
        _record_failure(admin_db, payment, remote.get("error_description")
                        or f"Razorpay reports this payment as '{remote_status}'.")
        raise HTTPException(
            status_code=400,
            detail="That payment did not go through. Please try again.",
        )

    from datetime import datetime, timezone
    settled = admin_db.table("payments").update({
        "razorpay_payment_id": razorpay_payment_id,
        "status": "paid",
        "signature_verified": True,
        "confirmed_via": via,
        "method": remote.get("method"),
        "paid_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", payment["id"]).execute()

    payment = settled.data[0] if settled.data else dict(payment, status="paid")

    _confirm_registration(admin_db, payment, actor=actor, via=via)
    return payment


def _record_failure(admin_db, payment: Dict[str, Any], description: str) -> None:
    """
    Note a failed attempt without closing the order.

    Left at 'failed' rather than deleted so the player can see that something
    was tried, and so an organiser fielding "I paid and it says unpaid" has the
    provider's own words in front of them. A fresh order is opened on the next
    attempt.
    """
    try:
        admin_db.table("payments").update({
            "status": "failed",
            "error_description": (description or "")[:500],
        }).eq("id", payment["id"]).execute()
    except Exception as e:
        logger.warning(f"Could not record payment failure for {payment.get('id')}: {str(e)}")


def _confirm_registration(admin_db, payment: Dict[str, Any],
                          actor: Optional[Dict[str, Any]], via: str) -> None:
    """
    Mark the entry paid and confirmed, and tell the entrants.

    Paying confirms the entry outright -- that is the point of charging up
    front. An organiser keeps /registrations/{id}/reject for an entry that
    should not stand, and a rejected entry that has been paid needs a refund
    from the Razorpay dashboard; nothing here refunds automatically.

    Never raises. The money has already moved and the payment row already says
    so; a failure to update the registration or send a notification must not
    turn into an error response that invites the player to pay a second time.
    """
    registration_id = payment.get("registration_id")
    try:
        rows = admin_db.table("registrations").select("*").eq(
            "id", registration_id).execute().data
        if not rows:
            logger.error(f"Paid registration {registration_id} has vanished.")
            return
        before = rows[0]

        if before.get("payment_status") == "paid" and before.get("status") == "approved":
            return

        updated = admin_db.table("registrations").update({
            "payment_status": "paid",
            "status": "approved",
        }).eq("id", registration_id).execute()
        after = updated.data[0] if updated.data else before

        record_audit(
            admin_db, actor=actor, action="registration.paid",
            entity_type="registration", entity_id=str(registration_id),
            previous_state=before, new_state=after,
            request_context={"confirmed_via": via,
                             "razorpay_payment_id": payment.get("razorpay_payment_id")},
        )

        name = _tournament_name(admin_db, before["tournament_id"])
        amount = int(payment.get("amount_paise") or 0) / 100
        fan_out_notification(
            admin_db,
            title="Entry Confirmed",
            message=(f"Your entry fee of ₹{amount:,.2f} for '{name}' has been received. "
                     f"You are now in the draw."),
            type="registration_confirmed",
            tournament_id=before["tournament_id"],
            recipient_ids=_participant_ids(admin_db, before),
        )
    except Exception as e:
        logger.error(f"Payment settled but confirming registration {registration_id} failed: {str(e)}")


@router.post("/verify")
async def verify_payment(data: PaymentVerifySchema, profile=Depends(get_user_profile)):
    """
    The browser came back from Checkout. Prove it, then confirm the entry.

    Every value in the body is attacker-controlled; the HMAC in
    `verify_payment_signature` is the whole basis for believing any of it, and
    the order id is looked up against a row THIS server created, so a signature
    over an order we never opened gets nowhere.
    """
    admin_db = get_admin_db()

    rows = admin_db.table("payments").select("*").eq(
        "razorpay_order_id", data.razorpay_order_id).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Unknown payment order.")
    payment = rows[0]

    registration = _load_registration(admin_db, payment["registration_id"])
    _authorise_payer(admin_db, registration, profile)

    if not razorpay_client.verify_payment_signature(
        data.razorpay_order_id, data.razorpay_payment_id, data.razorpay_signature
    ):
        logger.warning(
            "Rejected an unverifiable payment signature for order %s (caller %s)",
            data.razorpay_order_id, profile.get("id"),
        )
        record_audit(
            admin_db, actor=profile, action="payment.signature_rejected",
            entity_type="payment", entity_id=str(payment.get("id")),
            new_state={"order_id": data.razorpay_order_id,
                       "payment_id": data.razorpay_payment_id},
        )
        raise HTTPException(
            status_code=400,
            detail="This payment could not be verified. If money has left your "
                   "account, contact the organisers -- do not pay again.",
        )

    settled = await _settle_payment(
        admin_db, payment, data.razorpay_payment_id, via="callback", actor=profile
    )

    fresh = _load_registration(admin_db, payment["registration_id"])
    return {
        "payment": serialize_payment(settled),
        "registration": serialize_registration(fresh),
    }


@router.post("/webhook")
async def razorpay_webhook(request: Request):
    """
    Razorpay telling us directly what happened.

    Unauthenticated by necessity -- Razorpay has no account here -- so the
    signature is the only gate, and it is checked against the raw body before
    anything is parsed or trusted.

    Always answers 200 once the signature is good, even for events it ignores
    or cannot match to an order. A non-2xx makes Razorpay retry, and retrying
    an event we have no use for just fills the log.
    """
    admin_db = get_admin_db()

    raw = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    if not razorpay_client.webhook_configured():
        # Refuse rather than accept unverified. An open endpoint that marks
        # entries paid is worse than one that is switched off.
        logger.error("Razorpay webhook received but RAZORPAY_WEBHOOK_SECRET is not set.")
        raise HTTPException(status_code=503, detail="Webhook not configured.")

    if not razorpay_client.verify_webhook_signature(raw, signature):
        logger.warning("Rejected a Razorpay webhook with a bad signature.")
        raise HTTPException(status_code=400, detail="Invalid signature.")

    try:
        import json
        event = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Unreadable webhook body.")

    event_type = event.get("event") or ""
    entity = (((event.get("payload") or {}).get("payment") or {}).get("entity") or {})
    order_id = entity.get("order_id")
    payment_id = entity.get("id")

    if not order_id or event_type not in ("payment.captured", "payment.failed"):
        return {"status": "ignored", "event": event_type}

    rows = admin_db.table("payments").select("*").eq(
        "razorpay_order_id", order_id).execute().data
    if not rows:
        # An order this server did not create -- another environment pointed at
        # the same webhook, most likely. Acknowledged so it is not retried.
        logger.info(f"Webhook for unknown order {order_id}; ignoring.")
        return {"status": "unknown_order"}

    payment = rows[0]

    if event_type == "payment.failed":
        _record_failure(admin_db, payment,
                        entity.get("error_description") or "Payment failed.")
        return {"status": "recorded", "event": event_type}

    try:
        await _settle_payment(admin_db, payment, payment_id, via="webhook", actor=None)
    except HTTPException as e:
        # Do not make Razorpay retry a decision that will not change: a
        # mismatched amount or a foreign order is settled business.
        logger.warning(f"Webhook for order {order_id} not settled: {e.detail}")
        return {"status": "rejected", "reason": str(e.detail)}

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@router.get("/registrations/{registration_id}")
async def list_registration_payments(registration_id: str, profile=Depends(get_user_profile)):
    """Every attempt against one entry, newest first, for a receipt or a query."""
    admin_db = get_admin_db()
    registration = _load_registration(admin_db, registration_id)
    _authorise_payer(admin_db, registration, profile)

    rows = admin_db.table("payments").select("*").eq(
        "registration_id", registration_id).order("created_at", desc=True).execute().data

    return [serialize_payment(row) for row in (rows or [])]
