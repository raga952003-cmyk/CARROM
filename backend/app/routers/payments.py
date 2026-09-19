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


class LedgerUnreadable(Exception):
    """The payments table could not be read. Not the same as holding no rows."""


def _payments_for(admin_db, registration_id: str,
                  strict: bool = False) -> List[Dict[str, Any]]:
    """
    Every payment attempt against one entry.

    `strict` is for the two gates that decide whether to take money. Both ask
    "has this entry already been paid?", and both used to read an empty list
    when the table was simply unreadable -- a dropped connection, a missing
    migration, PostgREST refusing -- which answers "no prior payment" to a
    question whose safe answer is "I cannot tell". That is the exact condition
    under which a player is charged a second time.

    Left lenient everywhere else: a display path that cannot read the ledger
    should show nothing, not fail.
    """
    try:
        return admin_db.table("payments").select("*").eq(
            "registration_id", registration_id).execute().data or []
    except Exception as e:
        logger.error(f"Could not read payments for {registration_id}: {str(e)}")
        if strict:
            raise LedgerUnreadable(str(e))
        return []


# Tournament states in which no fee should be collected. Cancelled and
# completed are terminal: the event is not going to happen, or already has.
_UNPAYABLE_TOURNAMENT_STATES = ("cancelled", "completed")


def _why_not_approvable(admin_db, registration: Dict[str, Any]) -> Optional[str]:
    """
    Why this entry must not be approved by a payment, or None if it may be.

    Read at settle time rather than only at order time, because the gap
    between the two is a whole checkout -- easily long enough for an organiser
    to reject the entry or call the event off.
    """
    if registration.get("status") == "rejected":
        return "the entry had already been rejected by the organiser"

    rows = admin_db.table("tournaments").select("status, name").eq(
        "id", registration["tournament_id"]).execute().data
    if not rows:
        return "the tournament no longer exists"

    status = str(rows[0].get("status") or "")
    if status in _UNPAYABLE_TOURNAMENT_STATES:
        return f"the tournament is {status}"
    return None


def _flag_unexpected_payment(admin_db, payment: Dict[str, Any],
                             registration: Dict[str, Any], reason: str,
                             actor: Optional[Dict[str, Any]], via: str) -> None:
    """
    Money arrived that should not have. Record it, and tell the organiser.

    Deliberately NOT silent and deliberately not an approval: somebody is out
    of pocket and only the organiser can decide whether to refund or reinstate.
    The payment row already says 'paid', so the amount and the Razorpay id are
    on the ledger either way.
    """
    amount = int(payment.get("amount_paise") or 0) / 100
    logger.error(
        "Payment %s settled against registration %s but %s; entry NOT approved.",
        payment.get("razorpay_payment_id"), registration.get("id"), reason,
    )

    admin_db.table("registrations").update(
        {"payment_status": "paid"}
    ).eq("id", registration["id"]).execute()

    record_audit(
        admin_db, actor=actor, action="payment.needs_refund_decision",
        entity_type="registration", entity_id=str(registration["id"]),
        previous_state=registration,
        new_state={"payment_status": "paid", "status": registration.get("status")},
        request_context={"reason": reason, "confirmed_via": via,
                         "razorpay_payment_id": payment.get("razorpay_payment_id"),
                         "amount": amount},
    )

    try:
        owner_ids = _organiser_ids(admin_db, registration["tournament_id"])
        if owner_ids:
            fan_out_notification(
                admin_db,
                title="Payment needs a refund decision",
                message=(f"Rs {amount:,.2f} was received for an entry in "
                         f"'{_tournament_name(admin_db, registration['tournament_id'])}' "
                         f"but {reason}. The entry has NOT been approved. "
                         f"Refund it from the Razorpay dashboard, or reinstate the entry."),
                type="registration_confirmed",
                tournament_id=registration["tournament_id"],
                recipient_ids=owner_ids,
            )
    except Exception as e:
        logger.error(f"Could not notify the organiser about payment {payment.get('id')}: {str(e)}")


def _organiser_ids(admin_db, tournament_id: str) -> List[str]:
    """Whoever runs this tournament, for a message only they can act on."""
    try:
        rows = admin_db.table("tournaments").select("owner_id").eq(
            "id", tournament_id).execute().data
        return [rows[0]["owner_id"]] if rows and rows[0].get("owner_id") else []
    except Exception:
        # owner_id arrives with migration 003; without it there is nobody
        # specific to tell, and the audit record is still written.
        return []


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
        # Gated the same way keyId is. A server holding a live key_id whose
        # secret has not been deployed yet -- an ordinary half-finished
        # cutover -- would otherwise answer {"enabled": false, "liveMode":
        # true}, which reads as "this is the live system" to anything checking.
        "liveMode": configured and razorpay_client.is_live_mode(),
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

    # No webhook, no order. This refuses money rather than risking losing it.
    #
    # The browser callback is the happy path and the webhook is the ONLY other
    # way a completed payment reaches this server. A player whose tab closes,
    # whose phone rings, or whose connection drops between paying and being
    # redirected has genuinely paid -- Razorpay has their money -- and with no
    # webhook configured nothing here will ever hear about it. The entry stays
    # unpaid, the organiser sees no record, and the only trace is in Razorpay's
    # dashboard with nothing linking it to an entry.
    #
    # The gate above only checks the key pair, so a half-finished setup could
    # take payments in exactly that state. Checked here rather than at startup
    # because it is per-request configuration, and the message has to reach the
    # organiser who can fix it.
    if not razorpay_client.webhook_configured():
        logger.error(
            "Refusing to open a payment order: RAZORPAY_WEBHOOK_SECRET is not set, "
            "so a payment whose browser callback is lost could not be recorded. "
            "Create the webhook in the Razorpay dashboard and set the secret."
        )
        raise HTTPException(
            status_code=503,
            detail="Online payment is not fully set up for this event yet. "
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

    # Nothing is collected for an event that is over or called off. Registration
    # creation checks this; taking the fee never did, so a player could pay in
    # full for a tournament the organiser had already cancelled -- and end up
    # with a confirmed, paid entry in an event that was not happening.
    blocked = _why_not_approvable(admin_db, registration)
    if blocked:
        raise HTTPException(
            status_code=409,
            detail=f"This entry cannot be paid for because {blocked}.",
        )

    # An attempt that already succeeded, found before another order is minted.
    # _settle_payment refuses a second settlement anyway, but refusing here is
    # what stops the player reaching a checkout screen at all.
    # strict: an unreadable ledger must not read as "nothing paid yet", which
    # is what would send an already-paid player to checkout a second time.
    try:
        prior = _payments_for(admin_db, registration_id, strict=True)
    except LedgerUnreadable:
        raise HTTPException(
            status_code=503,
            detail="We cannot check whether this entry has already been paid, so "
                   "we will not open a payment. Please try again shortly.",
        )
    if any(row.get("status") == "paid" for row in prior):
        raise HTTPException(status_code=409, detail="This entry is already paid.")

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
    #
    # _confirm_registration still runs, rather than returning straight out.
    # Settling is two writes -- the payment row, then the registration -- and
    # the second one can fail on its own (a PostgREST 5xx, a dropped pooler
    # connection, the serverless instance being killed between them). That used
    # to leave an entry provably paid and permanently unconfirmed, because
    # every later redelivery returned here before reaching the repair. It is
    # idempotent, so calling it on an already-confirmed entry is a no-op.
    if payment.get("status") == "paid":
        _confirm_registration(admin_db, payment, actor=actor, via=via)
        return payment

    # A refunded row is terminal in the other direction, and must not be walked
    # back into 'paid'.
    #
    # `status` permits 'refunded' (015_payments.sql:72) and a refund is a
    # Dashboard action that nothing here initiates, so the row is the only
    # record on our side that the money went back. Without this, any later
    # redelivery of the original payment.captured -- Razorpay retries for
    # hours, and an organiser may refund well inside that window -- would find
    # a non-'paid' row, settle it again, and re-approve an entry that has been
    # refunded. The player would be in the draw having been given their money
    # back.
    if payment.get("status") == "refunded":
        logger.info(
            f"Ignoring a settlement for payment row {payment.get('id')}: "
            "it is already recorded as refunded."
        )
        return payment

    # Another payment has already settled this entry.
    #
    # The gate above is per payment ROW; this one is per REGISTRATION, which is
    # what actually protects the payer. One entry can have several live orders
    # at Razorpay -- a superseded order after a fee correction, a replacement
    # after a failed attempt, or two opened by a race -- and every one of them
    # stays payable until it expires. Without this, a player who completes an
    # abandoned checkout in an old tab pays a second time, in full, and every
    # check here passes because that payment genuinely does match its own order.
    #
    # Refused rather than quietly accepted, and recorded, so the organiser has
    # something to refund from.
    # strict again, and here it matters most: this is the gate the comment
    # above calls "what actually protects the payer". A 503 makes the webhook
    # redeliver, which is the right outcome -- better a delayed confirmation
    # than a second charge.
    try:
        siblings = _payments_for(admin_db, payment["registration_id"], strict=True)
    except LedgerUnreadable:
        raise HTTPException(
            status_code=503,
            detail="Could not check this entry for an earlier payment. "
                   "Not settling; this will be retried.",
        )
    already = [
        row for row in siblings
        if row.get("status") == "paid" and str(row.get("id")) != str(payment.get("id"))
    ]
    if already:
        logger.error(
            "Registration %s is already paid by payment %s; refusing to settle %s as well.",
            payment["registration_id"], already[0].get("razorpay_payment_id"),
            razorpay_payment_id,
        )
        record_audit(
            admin_db, actor=actor, action="payment.duplicate_refused",
            entity_type="payment", entity_id=str(payment.get("id")),
            new_state={"registration_id": payment["registration_id"],
                       "already_paid_by": already[0].get("razorpay_payment_id"),
                       "refused_payment_id": razorpay_payment_id},
        )
        raise HTTPException(
            status_code=409,
            detail="This entry has already been paid for. If you have been "
                   "charged twice, contact the organisers for a refund -- "
                   "do not pay again.",
        )

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

    # Only 'captured' is money taken.
    #
    # 'authorized' means the funds are held and not yet collected. It used to
    # be accepted here as success, which is wrong in a way that only surfaces
    # days later: an account whose capture setting is manual leaves every
    # payment authorized, so a whole field would be approved and drawn into
    # fixtures, and then Razorpay voids every uncaptured authorization after a
    # few days and the money never arrives. `payment_capture: 1` on the order
    # asks for auto-capture but does not guarantee the account honours it.
    #
    # So an authorization is recorded and left non-terminal: no paid_at, no
    # approval, and the entry stays unpaid until the payment.captured webhook
    # arrives -- which is exactly what that webhook is for.
    if remote_status == "authorized":
        logger.warning(
            "Payment %s is authorized but not captured; leaving the entry unpaid "
            "until capture. Check the account's capture setting if this persists.",
            razorpay_payment_id,
        )
        try:
            admin_db.table("payments").update({
                "razorpay_payment_id": razorpay_payment_id,
                "signature_verified": True,
                "method": remote.get("method"),
                "error_description": "Authorized but not yet captured.",
            }).eq("id", payment["id"]).execute()
        except Exception as e:
            logger.warning(f"Could not record the authorization for {payment.get('id')}: {str(e)}")
        raise HTTPException(
            status_code=402,
            detail="Your payment is authorised but not yet collected. Your entry "
                   "is confirmed as soon as it clears -- do not pay again.",
        )

    if remote_status != "captured":
        _record_failure(admin_db, payment, remote.get("error_description")
                        or f"Razorpay reports this payment as '{remote_status}'.")
        raise HTTPException(
            status_code=400,
            detail="That payment did not go through. Please try again.",
        )

    # Captured, but the money has since gone back.
    #
    # A full refund moves `status` to 'refunded' and is caught above; a partial
    # one does NOT -- the payment stays 'captured' and the refund shows only in
    # `amount_refunded` / `refund_status`. Settling on status alone therefore
    # confirms an entry for money the organiser has already returned, and the
    # amount check two blocks up does not catch it because `amount` is what was
    # charged, not what was kept.
    refunded_paise = int(remote.get("amount_refunded") or 0)
    if refunded_paise > 0 or remote.get("refund_status"):
        logger.warning(
            "Payment %s is captured but carries a refund of %s paise (refund_status=%s); "
            "not confirming the entry.",
            razorpay_payment_id, refunded_paise, remote.get("refund_status"),
        )
        _record_failure(
            admin_db, payment,
            f"Refunded ({refunded_paise} paise returned). The entry was not confirmed.",
            failed_payment_id=razorpay_payment_id,
        )
        raise HTTPException(
            status_code=400,
            detail="This payment has been refunded, so the entry was not confirmed. "
                   "Please contact the organisers.",
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


def _record_failure(admin_db, payment: Dict[str, Any], description: str,
                    failed_payment_id: Optional[str] = None) -> None:
    """
    Note a failed attempt without closing the order.

    Left at 'failed' rather than deleted so the player can see that something
    was tried, and so an organiser fielding "I paid and it says unpaid" has the
    provider's own words in front of them. A fresh order is opened on the next
    attempt.

    REFUSES to touch a row that has already settled. Razorpay Checkout lets a
    customer retry inside one order, so a single order can produce both a
    declined attempt and a captured one, and the failure webhook for the
    declined attempt routinely arrives AFTER the success -- delivery lags the
    browser callback, and a redelivery can trail it by hours. This used to
    overwrite the settled row: status flipped back to 'failed' on a payment
    that had genuinely been captured, so the ledger said the money never
    arrived while the entry stayed approved.

    `failed_payment_id` is the attempt the failure is about. When the row
    already records a DIFFERENT payment id, the failure belongs to a sibling
    attempt and is not this row's business either.
    """
    if payment.get("status") == "paid":
        logger.info(
            "Ignoring a failure for payment row %s: it has already settled as paid.",
            payment.get("id"),
        )
        return

    recorded = payment.get("razorpay_payment_id")
    if failed_payment_id and recorded and str(recorded) != str(failed_payment_id):
        logger.info(
            "Ignoring a failure for %s: row %s records attempt %s instead.",
            failed_payment_id, payment.get("id"), recorded,
        )
        return

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

    Two cases do NOT get approved, because in both of them the organiser has
    already decided the entry should not stand and a payment arriving late must
    not overturn that silently:

      * The entry was rejected. A player sitting in an open checkout window
        while the organiser rejects them used to be put straight back into the
        draw by finishing the payment -- reversing the organiser's decision,
        telling nobody, and being picked up by the next draw.
      * The tournament was cancelled or completed. Money can still arrive
        against an entry for an event that is no longer happening.

    Both are recorded as paid, so the money is on the ledger and refundable,
    and both notify the organiser rather than the player: the decision about
    what happens to that money is theirs.

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

        blocked = _why_not_approvable(admin_db, before)
        if blocked:
            _flag_unexpected_payment(admin_db, payment, before, blocked, actor=actor, via=via)
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

    # A capture carrying no payment id cannot be settled, and must not be
    # retried. _settle_payment fetches the payment from Razorpay BY that id, so
    # a None becomes GET /v1/payments/None -- a 400, which this handler turns
    # into a 5xx, which Razorpay reads as a failed delivery and redelivers.
    # Forever: a malformed payload does not improve on the next attempt.
    #
    # Acknowledged rather than refused, for the same reason an unknown order is.
    # The failure path below does NOT need this guard -- there payment_id is
    # only a discriminator telling one attempt from its siblings, and None
    # simply means "no sibling named".
    if event_type == "payment.captured" and not payment_id:
        logger.warning(
            f"Webhook payment.captured for order {order_id} carried no payment id; ignoring."
        )
        return {"status": "ignored", "event": event_type, "reason": "no payment id"}

    rows = admin_db.table("payments").select("*").eq(
        "razorpay_order_id", order_id).execute().data
    if not rows:
        # An order this server did not create -- another environment pointed at
        # the same webhook, most likely. Acknowledged so it is not retried.
        logger.info(f"Webhook for unknown order {order_id}; ignoring.")
        return {"status": "unknown_order"}

    payment = rows[0]

    if event_type == "payment.failed":
        # `payment_id` is passed so a failure for one attempt cannot overwrite
        # a sibling attempt that succeeded within the same order.
        _record_failure(admin_db, payment,
                        entity.get("error_description") or "Payment failed.",
                        failed_payment_id=payment_id)
        return {"status": "recorded", "event": event_type}

    try:
        await _settle_payment(admin_db, payment, payment_id, via="webhook", actor=None)
    except HTTPException as e:
        # A retry is worth having only when the answer might differ next time.
        #
        # 5xx means we could not reach Razorpay or our own database -- transient,
        # and Razorpay's redelivery is the ONLY thing that will settle this
        # payment when the browser callback never arrives. Answering 200 to that
        # told Razorpay the delivery succeeded and threw away the last chance to
        # record a payment that had genuinely been taken.
        #
        # 4xx is a decision: wrong amount, foreign order, an entry already paid.
        # Those will not change, so they are acknowledged rather than retried.
        if e.status_code >= 500:
            logger.error(
                "Webhook for order %s could not be settled (%s: %s); asking Razorpay to retry.",
                order_id, e.status_code, e.detail,
            )
            raise
        logger.warning(f"Webhook for order {order_id} not settled: {e.detail}")
        return {"status": "rejected", "reason": str(e.detail)}
    except Exception as e:
        # An unexpected failure is transient until proven otherwise. Same
        # reasoning: better a redelivery than a lost payment.
        logger.error(f"Webhook for order {order_id} raised {type(e).__name__}: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail="Could not record that payment just now. Please retry.",
        )

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
