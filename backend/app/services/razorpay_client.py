"""
Razorpay, over its REST API.

Deliberately NOT the `razorpay` pip package. The SDK is a thin wrapper over
three HTTP calls and an HMAC, and it drags in `requests` -- a second HTTP stack
alongside the `httpx` this app already carries -- into a serverless bundle that
has a measured 225 MB ceiling and has already failed a deploy for exactly this
reason (see the pandas note in requirements.txt). Everything below is the
public Orders and Payments API plus `hmac` from the standard library, so the
dependency list does not move.

Two things here are security-critical rather than merely functional:

  * `verify_payment_signature` is what separates a real payment from a POST
    somebody wrote by hand. The browser callback is attacker-controlled in
    full -- order id, payment id and signature all arrive from the client --
    and the HMAC is the only reason to believe any of it.
  * `verify_webhook_signature` uses a DIFFERENT secret from the payment
    signature: the webhook secret set when the webhook is created in the
    dashboard, not the API key secret. Getting these two confused fails closed
    (nothing verifies) rather than open, but it fails silently at 2am, so they
    are named apart and documented here.

Both comparisons use `hmac.compare_digest`. A `==` on the hex digest leaks the
correct prefix through timing, which is a real forgery path against a value an
attacker may submit as often as they like.
"""
import hashlib
import hmac
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger("uvicorn.error")

API_BASE = "https://api.razorpay.com/v1"

# Razorpay's own timeout is generous; ours is not. An order that takes longer
# than this to create is a player staring at a spinner, and the retry is
# cheaper than the wait.
TIMEOUT_SECONDS = 20


class RazorpayError(Exception):
    """
    A call to Razorpay that did not succeed.

    Carries the upstream status so the router can tell "Razorpay refused this"
    (surface it) from "Razorpay is down" (ask them to try again shortly).
    """

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def razorpay_configured() -> bool:
    """
    Whether both halves of the key pair are present.

    The key_id alone is not enough: it identifies the account but cannot
    authenticate a call or verify a signature, and a half-configured server
    that happily creates orders it can never verify is worse than one that
    admits up front it cannot take payments.
    """
    return bool(
        getattr(settings, "RAZORPAY_KEY_ID", "")
        and getattr(settings, "RAZORPAY_KEY_SECRET", "")
    )


def webhook_configured() -> bool:
    return bool(getattr(settings, "RAZORPAY_WEBHOOK_SECRET", ""))


def is_live_mode() -> bool:
    """
    Whether the configured key pair moves real money.

    Razorpay has no mode flag -- the environment is carried by the key prefix
    -- so this is the only way the application can know, and it is worth
    knowing: /api/health reports it, so nobody has to guess which mode a
    deployment is in by making a payment.
    """
    return str(getattr(settings, "RAZORPAY_KEY_ID", "")).startswith("rzp_live_")


def _auth() -> tuple:
    return (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)


def rupees_to_paise(rupees: Any) -> int:
    """
    Convert an entry fee to the integer paise Razorpay charges in.

    Rounded, not truncated: `int(19.99 * 100)` is 1998 in binary floating
    point, and an organiser who typed 19.99 would be silently undercharging by
    a paisa on every entry. `entry_fee` is NUMERIC in the database but arrives
    here as a float through PostgREST's JSON, so the rounding has to happen on
    this side.
    """
    return int(round(float(rupees or 0) * 100))


async def create_order(amount_paise: int, receipt: str,
                       notes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Open a Razorpay order for an amount this server decided.

    `receipt` is our own reference (the registration id), echoed back on the
    payment and visible in the dashboard, which is what makes a payment
    traceable to an entry when reconciling by hand.
    """
    if not razorpay_configured():
        raise RazorpayError("Razorpay is not configured on this server.")

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt[:40],  # Razorpay caps the receipt at 40 characters.
        "notes": notes or {},
        # Capture immediately rather than authorising and capturing later.
        # A two-step capture needs a second call that nothing here makes, and
        # an authorised-but-uncaptured payment auto-refunds after a few days --
        # which would look, from the app, like a confirmed entry that quietly
        # unpaid itself.
        "payment_capture": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(f"{API_BASE}/orders", json=payload, auth=_auth())
    except httpx.TimeoutException:
        raise RazorpayError("Razorpay did not respond in time. Please try again.")
    except Exception as e:
        logger.warning(f"Razorpay order creation failed to send: {str(e)}")
        raise RazorpayError("Could not reach Razorpay. Please try again.")

    if response.status_code >= 400:
        detail = _error_description(response)
        logger.warning(f"Razorpay refused order creation ({response.status_code}): {detail}")
        raise RazorpayError(detail, status=response.status_code)

    return response.json()


async def fetch_payment(payment_id: str) -> Dict[str, Any]:
    """
    Ask Razorpay what it thinks a payment actually was.

    This is the check that a verified signature does NOT give you. The
    signature proves the callback came from Razorpay about this order; it says
    nothing about the amount or the status, both of which the router compares
    against what it recorded before deciding an entry is paid.
    """
    if not razorpay_configured():
        raise RazorpayError("Razorpay is not configured on this server.")

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(f"{API_BASE}/payments/{payment_id}", auth=_auth())
    except httpx.TimeoutException:
        raise RazorpayError("Razorpay did not respond in time. Please try again.")
    except Exception as e:
        logger.warning(f"Razorpay payment fetch failed to send: {str(e)}")
        raise RazorpayError("Could not reach Razorpay. Please try again.")

    if response.status_code >= 400:
        detail = _error_description(response)
        raise RazorpayError(detail, status=response.status_code)

    return response.json()


def _error_description(response: httpx.Response) -> str:
    """Razorpay's own message for a failure, or a readable stand-in."""
    try:
        body = response.json()
        error = body.get("error") or {}
        return error.get("description") or str(body)[:200]
    except Exception:
        return (response.text or "Razorpay returned an unreadable error.")[:200]


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """
    Whether this callback really came from Razorpay for this order.

    The signed message is `order_id|payment_id`, keyed with the API key secret.
    Returns False rather than raising on missing input: an absent signature is
    an unverified payment, which is the same outcome as a wrong one, and the
    caller has one branch for both.
    """
    if not razorpay_configured() or not (order_id and payment_id and signature):
        return False

    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode("utf-8"),
        f"{order_id}|{payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return _digests_equal(expected, signature)


def _digests_equal(expected: str, provided: str) -> bool:
    """
    Constant-time compare of a hex digest against whatever the caller sent.

    Compared as BYTES, not as str. `hmac.compare_digest` on two `str` values
    raises TypeError the moment either holds a character outside ASCII -- and
    the signature is attacker-supplied: Starlette decodes request headers as
    latin-1, so a webhook delivery carrying `x-razorpay-signature: e` with an
    accent turned an unauthenticated rejection into an unhandled 500. Razorpay
    then treats the 5xx as a failed delivery and retries it, so a single
    malformed header becomes a retry loop against an endpoint that was never
    going to accept it.

    A genuine signature is lowercase hex and therefore pure ASCII, so anything
    that will not encode as ASCII cannot match and is simply not equal. The
    comparison itself stays constant-time for inputs of the right shape.
    """
    if not isinstance(expected, str) or not isinstance(provided, str):
        return False
    try:
        return hmac.compare_digest(expected.encode("ascii"), provided.encode("ascii"))
    except UnicodeEncodeError:
        return False


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """
    Whether this webhook really came from Razorpay.

    Keyed with RAZORPAY_WEBHOOK_SECRET -- set when the webhook is created in
    the dashboard, and NOT the same value as the API key secret -- over the
    exact bytes of the request body.

    "Exact bytes" is load-bearing: re-serialising the parsed JSON changes key
    order and whitespace, and the HMAC then never matches. The router reads the
    raw body for this reason.
    """
    if not webhook_configured() or not signature or raw_body is None:
        return False

    expected = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return _digests_equal(expected, signature)
