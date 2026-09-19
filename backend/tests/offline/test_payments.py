"""
Entry fees: the money path, over HTTP, against the in-memory database.

    POST /payments/registrations/{id}/order
    POST /payments/verify
    POST /payments/webhook
    GET  /payments/config

Technique: BLACK BOX along the player's path -- register, order, pay, confirmed
-- and then WHITE BOX at every point where believing the client would cost real
money. Those are the cases that matter here, so they are the ones enumerated:
a forged signature, a payment for somebody else's order, an amount that does
not match, a replayed webhook, a second verify of a payment already settled.

Razorpay itself is stubbed. `create_order` and `fetch_payment` are the only two
calls that leave the process, and they are replaced with functions that return
what Razorpay would -- including, deliberately, the wrong things, which is how
the amount and order-substitution checks get exercised at all. The HMAC is NOT
stubbed: signatures in these tests are computed with the real key the way
Razorpay would compute them, so `verify_payment_signature` is under test rather
than mocked out.

    python tests/offline/test_payments.py
"""
import hashlib
import hmac
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness                        # noqa: E402
from app.config import settings                    # noqa: E402
import app.services.razorpay_client as rzp         # noqa: E402

RESULTS = {}

KEY_ID = "rzp_test_offlinekey"
KEY_SECRET = "offline_key_secret"
WEBHOOK_SECRET = "offline_webhook_secret"


def check(label, cond, example=""):
    slot = RESULTS.setdefault(label, [0, 0, []])
    slot[1] += 1
    if not cond:
        slot[0] += 1
        if len(slot[2]) < 3:
            slot[2].append(str(example)[:300])
    return bool(cond)


def body(r):
    try:
        return r.json()
    except Exception:
        return r.text


def detail(r):
    p = body(r)
    return str(p.get("detail", p)) if isinstance(p, dict) else str(p)


# ---------------------------------------------------------------------------
# A stand-in for Razorpay
# ---------------------------------------------------------------------------

class FakeRazorpay:
    """
    Razorpay's two outbound calls, and a record of what was asked of them.

    `orders` is what create_order was told to charge -- the assertion that the
    server, not the client, sets the amount reads it directly.
    """

    def __init__(self):
        self.orders = {}          # order_id -> {"amount": paise, "receipt": ...}
        self.payments = {}        # payment_id -> the payment entity
        self.order_seq = 0
        self.create_calls = 0

    async def create_order(self, amount_paise, receipt, notes=None):
        self.create_calls += 1
        self.order_seq += 1
        order_id = "order_%08d" % self.order_seq
        self.orders[order_id] = {
            "id": order_id, "amount": amount_paise, "currency": "INR",
            "receipt": receipt, "notes": notes or {}, "status": "created",
        }
        return dict(self.orders[order_id])

    async def fetch_payment(self, payment_id):
        if payment_id not in self.payments:
            raise rzp.RazorpayError("payment not found", status=404)
        return dict(self.payments[payment_id])

    # -- test-side helpers -------------------------------------------------
    def pay(self, order_id, payment_id="pay_00000001", amount=None,
            status="captured", method="upi"):
        """Money moves. Returns the entity Razorpay would then report."""
        amount = self.orders[order_id]["amount"] if amount is None else amount
        self.payments[payment_id] = {
            "id": payment_id, "order_id": order_id, "amount": amount,
            "currency": "INR", "status": status, "method": method,
        }
        return self.payments[payment_id]


def sign_callback(order_id, payment_id, secret=KEY_SECRET):
    """The HMAC Razorpay Checkout would hand the browser."""
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(),
                    hashlib.sha256).hexdigest()


def sign_webhook(raw, secret=WEBHOOK_SECRET):
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def webhook_event(event, order_id, payment_id, amount=None, error=None):
    payload = {
        "event": event,
        "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": order_id, "amount": amount,
            "status": "captured" if event == "payment.captured" else "failed",
            "error_description": error,
        }}},
    }
    return json.dumps(payload).encode("utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def setup(entry_fee=500.0, configure=True, webhook=True):
    """
    A tournament open for registration, one player, Razorpay stubbed.

    Returns (harness, fake, organiser_id, player_id, tournament_id).
    """
    h = Harness()
    fake = FakeRazorpay()

    # Both the stubbed calls and the settings are restored in teardown. The
    # suites share one process, so a leaked key here would silently change what
    # every later suite's /api/health reports.
    h._rzp_orig = (rzp.create_order, rzp.fetch_payment)
    h._settings_orig = (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET,
                        settings.RAZORPAY_WEBHOOK_SECRET)
    rzp.create_order = fake.create_order
    rzp.fetch_payment = fake.fetch_payment

    settings.RAZORPAY_KEY_ID = KEY_ID if configure else ""
    settings.RAZORPAY_KEY_SECRET = KEY_SECRET if configure else ""
    settings.RAZORPAY_WEBHOOK_SECRET = WEBHOOK_SECRET if webhook else ""

    organiser = h.make_user("Org Anne", role="admin")
    player = h.make_user("Pat Player", role="player")

    tid = h.seed_tournament(
        organiser, name="Fee Cup", status="registration_open",
        entry_fee=entry_fee, category="singles",
        registration_start_date="2020-01-01", registration_end_date="2099-01-01",
    )
    return h, fake, organiser, player, tid


def teardown(h):
    rzp.create_order, rzp.fetch_payment = h._rzp_orig
    (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET,
     settings.RAZORPAY_WEBHOOK_SECRET) = h._settings_orig


def register(h, tid, player):
    r = h.post("/api/tournaments/%s/registrations" % tid,
               {"type": "singles", "playerId": player}, user_id=player)
    return r


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

def test_pay_then_confirmed():
    h, fake, org, player, tid = setup(entry_fee=500.0)
    try:
        r = register(h, tid, player)
        check("registering with a fee succeeds", r.status_code == 200, detail(r))
        reg = body(r)
        check("an entry with a fee starts unpaid",
              reg.get("paymentStatus") == "pending", reg)
        check("an entry with a fee starts unapproved",
              reg.get("status") == "pending", reg)
        check("the fee is snapshotted onto the entry in paise",
              reg.get("feePaise") == 50000, reg)

        o = h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player)
        check("the player can open an order", o.status_code == 200, detail(o))
        order = body(o)

        # The assertion this whole design exists for.
        check("the amount charged is the server's figure, not the client's",
              fake.orders[order["orderId"]]["amount"] == 50000,
              fake.orders.get(order["orderId"]))
        check("the order quotes the amount in paise", order.get("amount") == 50000, order)
        check("the browser is given the key id", order.get("keyId") == KEY_ID, order)

        fake.pay(order["orderId"], "pay_success")
        v = h.post("/api/payments/verify", {
            "razorpay_order_id": order["orderId"],
            "razorpay_payment_id": "pay_success",
            "razorpay_signature": sign_callback(order["orderId"], "pay_success"),
        }, user_id=player)
        check("a correctly signed payment verifies", v.status_code == 200, detail(v))

        after = body(v).get("registration") or {}
        check("paying marks the entry paid", after.get("paymentStatus") == "paid", after)
        check("paying confirms the entry", after.get("status") == "approved", after)

        payment = body(v).get("payment") or {}
        check("the payment is recorded as paid", payment.get("status") == "paid", payment)
        check("the signature is recorded as verified",
              payment.get("signatureVerified") is True, payment)
        check("the confirming path is recorded",
              payment.get("confirmedVia") == "callback", payment)
        check("the payment reports rupees for display", payment.get("amount") == 500.0, payment)

        notes = [n for n in h.db.rows("notifications") if n.get("profile_id") == player]
        check("the player is told their entry is confirmed",
              any("confirm" in (n.get("title") or "").lower() for n in notes), notes)
    finally:
        teardown(h)


def test_free_tournament_needs_no_payment():
    h, fake, org, player, tid = setup(entry_fee=0.0)
    try:
        reg = body(register(h, tid, player))
        check("a free entry is waived rather than left pending",
              reg.get("paymentStatus") == "waived", reg)

        o = h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player)
        check("a free entry cannot open an order", o.status_code == 409, detail(o))
        check("no order is created for a free entry", fake.create_calls == 0, fake.create_calls)
    finally:
        teardown(h)


def test_admin_entry_is_waived():
    h, fake, org, player, tid = setup(entry_fee=500.0)
    try:
        r = h.post("/api/tournaments/%s/registrations" % tid,
                   {"type": "singles", "playerId": player}, user_id=org)
        reg = body(r)
        check("an organiser's own entry is approved", reg.get("status") == "approved", reg)
        check("an organiser entering somebody takes the money in person",
              reg.get("paymentStatus") == "waived", reg)
    finally:
        teardown(h)


# ---------------------------------------------------------------------------
# Forgery and substitution
# ---------------------------------------------------------------------------

def test_forged_signature_is_refused():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(order["orderId"], "pay_forged")

        for label, signature in (
            ("a signature signed with the wrong secret",
             sign_callback(order["orderId"], "pay_forged", secret="not_the_secret")),
            ("a made-up signature", "deadbeef" * 8),
            ("an empty signature", ""),
            ("a signature over a different payment id",
             sign_callback(order["orderId"], "pay_somethingelse")),
        ):
            v = h.post("/api/payments/verify", {
                "razorpay_order_id": order["orderId"],
                "razorpay_payment_id": "pay_forged",
                "razorpay_signature": signature,
            }, user_id=player)
            check("verify refuses %s" % label, v.status_code == 400, detail(v))

        rows = h.db.rows("registrations")
        check("a refused payment leaves the entry unpaid",
              all(r.get("payment_status") == "pending" for r in rows), rows)
        check("a refused payment leaves the entry unapproved",
              all(r.get("status") == "pending" for r in rows), rows)
    finally:
        teardown(h)


def test_amount_mismatch_is_refused():
    """
    A validly signed payment for the wrong amount.

    The signature covers only the two ids, so this passes the HMAC and must be
    caught by the amount comparison against what was recorded at order time.
    """
    h, fake, org, player, tid = setup(entry_fee=500.0)
    try:
        reg = body(register(h, tid, player))
        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))

        fake.pay(order["orderId"], "pay_cheap", amount=100)   # ₹1 against a ₹500 fee
        v = h.post("/api/payments/verify", {
            "razorpay_order_id": order["orderId"],
            "razorpay_payment_id": "pay_cheap",
            "razorpay_signature": sign_callback(order["orderId"], "pay_cheap"),
        }, user_id=player)

        check("a signed payment for the wrong amount is refused", v.status_code == 400, detail(v))
        check("the entry stays unpaid after an underpayment",
              h.db.rows("registrations")[0].get("payment_status") == "pending",
              h.db.rows("registrations"))
    finally:
        teardown(h)


def test_payment_for_another_order_is_refused():
    """One player's real payment, presented against another entry of theirs."""
    h, fake, org, player, tid = setup(entry_fee=500.0)
    try:
        second = h.seed_tournament(
            org, name="Cheap Cup", status="registration_open", entry_fee=500.0,
            registration_start_date="2020-01-01", registration_end_date="2099-01-01",
            id="33333333-3333-3333-3333-333333333333",
        )
        reg_a = body(register(h, tid, player))
        reg_b = body(register(h, second, player))

        order_a = body(h.post("/api/payments/registrations/%s/order" % reg_a["id"], {}, user_id=player))
        order_b = body(h.post("/api/payments/registrations/%s/order" % reg_b["id"], {}, user_id=player))

        # Really paid, but for B.
        fake.pay(order_b["orderId"], "pay_for_b")

        v = h.post("/api/payments/verify", {
            "razorpay_order_id": order_a["orderId"],
            "razorpay_payment_id": "pay_for_b",
            "razorpay_signature": sign_callback(order_a["orderId"], "pay_for_b"),
        }, user_id=player)

        check("a payment belonging to another order is refused", v.status_code == 400, detail(v))
        regs = {r["id"]: r for r in h.db.rows("registrations")}
        check("the unpaid entry stays unpaid",
              regs[reg_a["id"]].get("payment_status") == "pending", regs[reg_a["id"]])
    finally:
        teardown(h)


def test_failed_payment_is_refused():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(order["orderId"], "pay_failed", status="failed")

        v = h.post("/api/payments/verify", {
            "razorpay_order_id": order["orderId"],
            "razorpay_payment_id": "pay_failed",
            "razorpay_signature": sign_callback(order["orderId"], "pay_failed"),
        }, user_id=player)

        check("a payment Razorpay reports as failed is refused", v.status_code == 400, detail(v))
        check("the failed attempt is recorded",
              any(p.get("status") == "failed" for p in h.db.rows("payments")),
              h.db.rows("payments"))
        check("a failed payment does not confirm the entry",
              h.db.rows("registrations")[0].get("status") == "pending",
              h.db.rows("registrations"))
    finally:
        teardown(h)


def test_verify_for_an_order_we_never_opened():
    h, fake, org, player, tid = setup()
    try:
        v = h.post("/api/payments/verify", {
            "razorpay_order_id": "order_invented",
            "razorpay_payment_id": "pay_invented",
            "razorpay_signature": sign_callback("order_invented", "pay_invented"),
        }, user_id=player)
        # Correctly signed, but against an order this server never created --
        # so there is nothing to confirm, whoever signed it.
        check("verify refuses an order this server never opened",
              v.status_code == 404, detail(v))
    finally:
        teardown(h)


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------

def test_only_the_entrant_may_pay():
    h, fake, org, player, tid = setup()
    try:
        stranger = h.make_user("Sam Stranger", role="player")
        reg = body(register(h, tid, player))

        o = h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=stranger)
        check("a stranger cannot open an order against somebody else's entry",
              o.status_code == 403, detail(o))

        o2 = h.post("/api/payments/registrations/%s/order" % reg["id"], {})
        check("an anonymous caller cannot open an order", o2.status_code == 401, detail(o2))

        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(order["orderId"], "pay_x")
        v = h.post("/api/payments/verify", {
            "razorpay_order_id": order["orderId"],
            "razorpay_payment_id": "pay_x",
            "razorpay_signature": sign_callback(order["orderId"], "pay_x"),
        }, user_id=stranger)
        check("a stranger cannot verify somebody else's payment",
              v.status_code == 403, detail(v))

        ls = h.get("/api/payments/registrations/%s" % reg["id"], user_id=stranger)
        check("a stranger cannot read somebody else's payments",
              ls.status_code == 403, detail(ls))
    finally:
        teardown(h)


def test_the_organiser_may_take_a_payment():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        o = h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=org)
        check("the tournament's organiser may open an order at the desk",
              o.status_code == 200, detail(o))
    finally:
        teardown(h)


# ---------------------------------------------------------------------------
# Idempotency -- the property the webhook retries depend on
# ---------------------------------------------------------------------------

def test_verify_twice_confirms_once():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(order["orderId"], "pay_double")

        payload = {
            "razorpay_order_id": order["orderId"],
            "razorpay_payment_id": "pay_double",
            "razorpay_signature": sign_callback(order["orderId"], "pay_double"),
        }
        first = h.post("/api/payments/verify", payload, user_id=player)
        second = h.post("/api/payments/verify", payload, user_id=player)

        check("verifying twice is not an error", first.status_code == 200
              and second.status_code == 200, (detail(first), detail(second)))
        check("verifying twice leaves one payment row",
              len(h.db.rows("payments")) == 1, h.db.rows("payments"))
        check("the entry is confirmed exactly once",
              h.db.rows("registrations")[0].get("status") == "approved",
              h.db.rows("registrations"))

        confirmations = [n for n in h.db.rows("notifications")
                         if n.get("profile_id") == player
                         and "confirm" in (n.get("title") or "").lower()]
        check("the player is not told twice", len(confirmations) == 1, confirmations)
    finally:
        teardown(h)


def test_reopening_an_order_reuses_it():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        first = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        second = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))

        check("re-opening returns the same order",
              first["orderId"] == second["orderId"], (first, second))
        check("re-opening does not create a second order at Razorpay",
              fake.create_calls == 1, fake.create_calls)
        check("re-opening does not create a second payment row",
              len(h.db.rows("payments")) == 1, h.db.rows("payments"))
    finally:
        teardown(h)


def test_paid_entry_cannot_be_ordered_again():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(order["orderId"], "pay_once")
        h.post("/api/payments/verify", {
            "razorpay_order_id": order["orderId"],
            "razorpay_payment_id": "pay_once",
            "razorpay_signature": sign_callback(order["orderId"], "pay_once"),
        }, user_id=player)

        again = h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player)
        check("a paid entry cannot open another order", again.status_code == 409, detail(again))
    finally:
        teardown(h)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

def test_webhook_confirms_without_the_browser():
    """The player closed the tab. The entry must still be confirmed."""
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(order["orderId"], "pay_webhook")

        raw = webhook_event("payment.captured", order["orderId"], "pay_webhook", 50000)
        w = h.client.post("/api/payments/webhook", content=raw,
                          headers={"x-razorpay-signature": sign_webhook(raw),
                                   "content-type": "application/json"})

        check("a signed webhook is accepted", w.status_code == 200, detail(w))
        check("the webhook confirms the entry",
              h.db.rows("registrations")[0].get("status") == "approved",
              h.db.rows("registrations"))
        check("the webhook records how it was confirmed",
              h.db.rows("payments")[0].get("confirmed_via") == "webhook",
              h.db.rows("payments"))
    finally:
        teardown(h)


def test_webhook_signature_is_required():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(order["orderId"], "pay_unsigned")
        raw = webhook_event("payment.captured", order["orderId"], "pay_unsigned", 50000)

        for label, headers in (
            ("no signature", {"content-type": "application/json"}),
            ("a wrong signature", {"x-razorpay-signature": "00" * 32,
                                   "content-type": "application/json"}),
            ("a signature made with the API key secret rather than the webhook secret",
             {"x-razorpay-signature": sign_webhook(raw, secret=KEY_SECRET),
              "content-type": "application/json"}),
        ):
            w = h.client.post("/api/payments/webhook", content=raw, headers=headers)
            check("the webhook refuses %s" % label, w.status_code == 400, detail(w))

        check("an unverified webhook confirms nothing",
              h.db.rows("registrations")[0].get("status") == "pending",
              h.db.rows("registrations"))
    finally:
        teardown(h)


def test_webhook_body_must_not_be_reserialised():
    """
    The signature is over the exact bytes.

    Guards the one mistake that makes every webhook fail in production while
    every unit test passes: verifying against re-encoded JSON. The bytes here
    carry whitespace that `json.dumps` would not produce, so anything that
    parses and re-serialises before checking the HMAC fails this.
    """
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(order["orderId"], "pay_spaced")

        raw = json.dumps({
            "event": "payment.captured",
            "payload": {"payment": {"entity": {
                "id": "pay_spaced", "order_id": order["orderId"],
                "amount": 50000, "status": "captured",
            }}},
        }, indent=4).encode("utf-8")

        w = h.client.post("/api/payments/webhook", content=raw,
                          headers={"x-razorpay-signature": sign_webhook(raw),
                                   "content-type": "application/json"})
        check("a webhook signed over unusual whitespace still verifies",
              w.status_code == 200, detail(w))
    finally:
        teardown(h)


def test_webhook_replay_confirms_once():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(order["orderId"], "pay_replayed")
        raw = webhook_event("payment.captured", order["orderId"], "pay_replayed", 50000)
        headers = {"x-razorpay-signature": sign_webhook(raw),
                   "content-type": "application/json"}

        codes = [h.client.post("/api/payments/webhook", content=raw, headers=headers).status_code
                 for _ in range(4)]

        check("a redelivered webhook is always accepted",
              codes == [200, 200, 200, 200], codes)
        check("a redelivered webhook leaves one payment row",
              len(h.db.rows("payments")) == 1, h.db.rows("payments"))
        confirmations = [n for n in h.db.rows("notifications")
                         if "confirm" in (n.get("title") or "").lower()]
        check("a redelivered webhook notifies once", len(confirmations) == 1, confirmations)
    finally:
        teardown(h)


def test_callback_and_webhook_together_confirm_once():
    """Both paths fire for the same payment, which is the normal case."""
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(order["orderId"], "pay_both")

        h.post("/api/payments/verify", {
            "razorpay_order_id": order["orderId"],
            "razorpay_payment_id": "pay_both",
            "razorpay_signature": sign_callback(order["orderId"], "pay_both"),
        }, user_id=player)

        raw = webhook_event("payment.captured", order["orderId"], "pay_both", 50000)
        w = h.client.post("/api/payments/webhook", content=raw,
                          headers={"x-razorpay-signature": sign_webhook(raw),
                                   "content-type": "application/json"})

        check("the webhook after a callback is accepted", w.status_code == 200, detail(w))
        check("the callback's record is not overwritten by the webhook",
              h.db.rows("payments")[0].get("confirmed_via") == "callback",
              h.db.rows("payments"))
        confirmations = [n for n in h.db.rows("notifications")
                         if "confirm" in (n.get("title") or "").lower()]
        check("callback plus webhook notifies once", len(confirmations) == 1, confirmations)
    finally:
        teardown(h)


def test_webhook_records_a_failure():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        raw = webhook_event("payment.failed", order["orderId"], "pay_nope",
                            error="Your card was declined.")
        w = h.client.post("/api/payments/webhook", content=raw,
                          headers={"x-razorpay-signature": sign_webhook(raw),
                                   "content-type": "application/json"})

        check("a failure webhook is accepted", w.status_code == 200, detail(w))
        row = h.db.rows("payments")[0]
        check("the failure is recorded", row.get("status") == "failed", row)
        check("Razorpay's own words are kept",
              "declined" in (row.get("error_description") or ""), row)
        check("a failed payment does not confirm the entry",
              h.db.rows("registrations")[0].get("status") == "pending",
              h.db.rows("registrations"))
    finally:
        teardown(h)


def test_webhook_for_an_unknown_order_is_acknowledged():
    """Another environment pointed at this webhook. Ack it, do not retry it."""
    h, fake, org, player, tid = setup()
    try:
        raw = webhook_event("payment.captured", "order_from_elsewhere", "pay_elsewhere", 100)
        w = h.client.post("/api/payments/webhook", content=raw,
                          headers={"x-razorpay-signature": sign_webhook(raw),
                                   "content-type": "application/json"})
        check("an unknown order is acknowledged rather than retried",
              w.status_code == 200, detail(w))
        check("an unknown order writes nothing", h.db.rows("payments") == [],
              h.db.rows("payments"))
    finally:
        teardown(h)


def test_webhook_refuses_when_no_secret_is_configured():
    h, fake, org, player, tid = setup(webhook=False)
    try:
        raw = webhook_event("payment.captured", "order_x", "pay_x", 100)
        w = h.client.post("/api/payments/webhook", content=raw,
                          headers={"x-razorpay-signature": "whatever",
                                   "content-type": "application/json"})
        check("an unconfigured webhook refuses rather than trusting the caller",
              w.status_code == 503, detail(w))
    finally:
        teardown(h)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_unconfigured_server_says_so():
    h, fake, org, player, tid = setup(configure=False)
    try:
        c = body(h.get("/api/payments/config", user_id=player))
        check("an unconfigured server reports payments off", c.get("enabled") is False, c)
        check("an unconfigured server hands out no key", not c.get("keyId"), c)

        reg = body(register(h, tid, player))
        o = h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player)
        check("an unconfigured server refuses to open an order",
              o.status_code == 503, detail(o))
        check("the refusal tells the player what to do instead",
              "organiser" in detail(o).lower(), detail(o))
    finally:
        teardown(h)


def test_half_configured_server_is_treated_as_off():
    """
    A key_id with no secret.

    The exact state the repository was in before this feature existed, and the
    dangerous one: enough to open a checkout, not enough to verify a thing that
    comes back from it.
    """
    h, fake, org, player, tid = setup()
    try:
        settings.RAZORPAY_KEY_SECRET = ""
        c = body(h.get("/api/payments/config", user_id=player))
        check("a key id with no secret counts as unconfigured",
              c.get("enabled") is False, c)
        check("a key id with no secret is not handed to the browser",
              not c.get("keyId"), c)
    finally:
        settings.RAZORPAY_KEY_SECRET = KEY_SECRET
        teardown(h)


def test_mode_is_reported():
    h, fake, org, player, tid = setup()
    try:
        c = body(h.get("/api/payments/config", user_id=player))
        check("a test key reports test mode", c.get("liveMode") is False, c)

        settings.RAZORPAY_KEY_ID = "rzp_live_something"
        c2 = body(h.get("/api/payments/config", user_id=player))
        check("a live key reports live mode", c2.get("liveMode") is True, c2)

        health = body(h.get("/api/health"))
        check("health names the mode the deployment is in",
              (health.get("payments") or {}).get("mode") == "live", health.get("payments"))
    finally:
        settings.RAZORPAY_KEY_ID = KEY_ID
        teardown(h)


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------

def test_rupees_to_paise():
    cases = [
        (0, 0), (1, 100), (500, 50000), (500.0, 50000),
        # The float-rounding case: int(19.99 * 100) is 1998.
        (19.99, 1999),
        (0.01, 1), (1234.56, 123456), (None, 0),
        # A third of a rupee cannot be charged; it rounds rather than truncating.
        (0.335, 34) if round(0.335 * 100) == 34 else (0.335, 33),
    ]
    for rupees, expected in cases:
        got = rzp.rupees_to_paise(rupees)
        check("rupees convert to paise without losing money",
              got == expected, "%r -> %r, expected %r" % (rupees, got, expected))


def test_fee_change_does_not_move_an_existing_entry():
    """An organiser raises the fee. Whoever already entered owes the old price."""
    h, fake, org, player, tid = setup(entry_fee=500.0)
    try:
        reg = body(register(h, tid, player))

        for row in h.db.tables["tournaments"]:
            if row["id"] == tid:
                row["entry_fee"] = 900.0

        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        check("an entry made at the old fee is charged the old fee",
              fake.orders[order["orderId"]]["amount"] == 50000,
              fake.orders[order["orderId"]])
    finally:
        teardown(h)


def test_entry_without_the_migration_falls_back_to_the_current_fee():
    """
    A database that has not had migration 015 applied.

    The entry is still taken -- an organiser mid-event must not lose
    registrations to a missing migration -- and the fee falls back to the
    tournament's current figure.
    """
    h, fake, org, player, tid = setup(entry_fee=500.0)
    try:
        reg = body(register(h, tid, player))
        # Simulate the pre-015 row: no snapshot column at all.
        for row in h.db.tables["registrations"]:
            row.pop("fee_paise", None)

        order = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        check("an entry with no snapshot is charged the tournament's fee",
              fake.orders[order["orderId"]]["amount"] == 50000,
              fake.orders[order["orderId"]])
    finally:
        teardown(h)



# ---------------------------------------------------------------------------
# The double-charge guard
#
# Settling used to be idempotent per payment ROW, which is not the same thing
# as per ENTRY. One registration can have several live orders at Razorpay -- a
# superseded one after a fee correction, a replacement after a failed attempt,
# two from a race -- and each stays payable until it expires. Completing an
# abandoned checkout in an old tab therefore took a second full payment, and
# every check passed, because that payment really did match its own order.
# ---------------------------------------------------------------------------

def test_a_second_order_cannot_take_a_second_payment():
    h, fake, org, player, tid = setup(entry_fee=500.0)
    try:
        reg = body(register(h, tid, player))
        first = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))

        # A failed attempt leaves order A payable at Razorpay and opens order B
        # on the next try -- the documented behaviour of _record_failure.
        fake.pay(first["orderId"], "pay_declined", status="failed")
        h.post("/api/payments/verify", {
            "razorpay_order_id": first["orderId"],
            "razorpay_payment_id": "pay_declined",
            "razorpay_signature": sign_callback(first["orderId"], "pay_declined"),
        }, user_id=player)

        second = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        check("a fresh order is opened after a failed attempt",
              second["orderId"] != first["orderId"], (first, second))

        # Pay the replacement: the entry is now settled.
        fake.pay(second["orderId"], "pay_good")
        ok = h.post("/api/payments/verify", {
            "razorpay_order_id": second["orderId"],
            "razorpay_payment_id": "pay_good",
            "razorpay_signature": sign_callback(second["orderId"], "pay_good"),
        }, user_id=player)
        check("the replacement payment settles", ok.status_code == 200, detail(ok))

        # Now finish the abandoned first checkout. Real money, real order, real
        # signature -- and it must still be refused.
        fake.pay(first["orderId"], "pay_second_charge")
        dup = h.post("/api/payments/verify", {
            "razorpay_order_id": first["orderId"],
            "razorpay_payment_id": "pay_second_charge",
            "razorpay_signature": sign_callback(first["orderId"], "pay_second_charge"),
        }, user_id=player)

        check("a second payment for an already-paid entry is refused",
              dup.status_code == 409, detail(dup))
        check("the refusal tells the player not to pay again",
              "do not pay again" in detail(dup).lower(), detail(dup))

        paid = [p for p in h.db.rows("payments") if p.get("status") == "paid"]
        check("only one payment row is ever marked paid", len(paid) == 1, h.db.rows("payments"))
        check("the duplicate is recorded for the organiser to refund",
              any(a.get("action") == "payment.duplicate_refused" for a in h.db.rows("audit_logs")),
              [a.get("action") for a in h.db.rows("audit_logs")])
    finally:
        teardown(h)


def test_a_paid_entry_cannot_open_another_order():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        o = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(o["orderId"], "pay_done")
        h.post("/api/payments/verify", {
            "razorpay_order_id": o["orderId"],
            "razorpay_payment_id": "pay_done",
            "razorpay_signature": sign_callback(o["orderId"], "pay_done"),
        }, user_id=player)

        again = h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player)
        check("a paid entry is refused a new order before checkout can open",
              again.status_code == 409, detail(again))
    finally:
        teardown(h)


# ---------------------------------------------------------------------------
# A late failure must not erase a success
#
# Checkout lets a customer retry inside one order, so one order can produce a
# declined attempt AND a captured one -- and the failure webhook for the
# declined attempt routinely arrives after the success. This used to flip the
# settled row back to 'failed', so the ledger said the money never came while
# the entry stayed approved.
# ---------------------------------------------------------------------------

def test_a_late_failure_webhook_does_not_erase_a_settled_payment():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        o = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))

        fake.pay(o["orderId"], "pay_captured")
        h.post("/api/payments/verify", {
            "razorpay_order_id": o["orderId"],
            "razorpay_payment_id": "pay_captured",
            "razorpay_signature": sign_callback(o["orderId"], "pay_captured"),
        }, user_id=player)
        check("the payment settles first",
              h.db.rows("payments")[0].get("status") == "paid", h.db.rows("payments"))

        # The declined sibling attempt's failure arrives afterwards.
        raw = webhook_event("payment.failed", o["orderId"], "pay_declined_earlier",
                            error="Card declined.")
        w = h.client.post("/api/payments/webhook", content=raw,
                          headers={"x-razorpay-signature": sign_webhook(raw),
                                   "content-type": "application/json"})

        check("the late failure is accepted rather than retried", w.status_code == 200, detail(w))
        row = h.db.rows("payments")[0]
        check("a settled payment stays paid", row.get("status") == "paid", row)
        check("the settled payment id is not overwritten",
              row.get("razorpay_payment_id") == "pay_captured", row)
        check("the entry stays approved after a late failure",
              h.db.rows("registrations")[0].get("status") == "approved",
              h.db.rows("registrations"))
    finally:
        teardown(h)


def test_a_failure_for_a_different_attempt_is_ignored():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        o = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))

        # Record one attempt against the row without settling it.
        for row in h.db.tables["payments"]:
            row["razorpay_payment_id"] = "pay_attempt_one"

        raw = webhook_event("payment.failed", o["orderId"], "pay_attempt_two",
                            error="A different attempt failed.")
        h.client.post("/api/payments/webhook", content=raw,
                      headers={"x-razorpay-signature": sign_webhook(raw),
                               "content-type": "application/json"})

        row = h.db.rows("payments")[0]
        check("a failure naming another attempt does not touch this row",
              row.get("status") != "failed", row)
    finally:
        teardown(h)


# ---------------------------------------------------------------------------
# A payment must not overturn the organiser's decision
# ---------------------------------------------------------------------------

def test_paying_does_not_reinstate_a_rejected_entry():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        o = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))

        # The organiser rejects the still-unpaid entry while checkout is open.
        rj = h.post("/api/registrations/%s/reject" % reg["id"], {}, user_id=org)
        check("the organiser can reject the entry", rj.status_code == 200, detail(rj))

        # The player finishes paying anyway.
        fake.pay(o["orderId"], "pay_after_reject")
        v = h.post("/api/payments/verify", {
            "razorpay_order_id": o["orderId"],
            "razorpay_payment_id": "pay_after_reject",
            "razorpay_signature": sign_callback(o["orderId"], "pay_after_reject"),
        }, user_id=player)
        check("the payment itself is accepted", v.status_code == 200, detail(v))

        r = h.db.rows("registrations")[0]
        check("a rejected entry is NOT put back in the draw by paying",
              r.get("status") == "rejected", r)
        check("the money is still recorded against the entry",
              r.get("payment_status") == "paid", r)
        check("the organiser is asked to make a refund decision",
              any(a.get("action") == "payment.needs_refund_decision"
                  for a in h.db.rows("audit_logs")),
              [a.get("action") for a in h.db.rows("audit_logs")])
        check("the organiser is notified, not the player",
              any("refund" in (n.get("title") or "").lower() for n in h.db.rows("notifications")),
              [n.get("title") for n in h.db.rows("notifications")])
    finally:
        teardown(h)


def test_a_cancelled_tournament_collects_nothing():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        o = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))

        for row in h.db.tables["tournaments"]:
            if row["id"] == tid:
                row["status"] = "cancelled"

        # No new order for a cancelled event.
        blocked = h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player)
        check("a cancelled tournament refuses a new order",
              blocked.status_code == 409, detail(blocked))
        check("the refusal says why", "cancelled" in detail(blocked).lower(), detail(blocked))

        # And a payment already in flight does not confirm an entry.
        fake.pay(o["orderId"], "pay_cancelled")
        h.post("/api/payments/verify", {
            "razorpay_order_id": o["orderId"],
            "razorpay_payment_id": "pay_cancelled",
            "razorpay_signature": sign_callback(o["orderId"], "pay_cancelled"),
        }, user_id=player)

        r = h.db.rows("registrations")[0]
        check("a payment for a cancelled event does not approve the entry",
              r.get("status") != "approved", r)
        check("the money is still on the record", r.get("payment_status") == "paid", r)
    finally:
        teardown(h)


# ---------------------------------------------------------------------------
# Authorized is not captured
# ---------------------------------------------------------------------------

def test_an_authorized_payment_does_not_confirm_the_entry():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        o = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))

        fake.pay(o["orderId"], "pay_held", status="authorized")
        v = h.post("/api/payments/verify", {
            "razorpay_order_id": o["orderId"],
            "razorpay_payment_id": "pay_held",
            "razorpay_signature": sign_callback(o["orderId"], "pay_held"),
        }, user_id=player)

        check("an authorized-but-uncaptured payment is not treated as paid",
              v.status_code == 402, detail(v))
        check("the player is told not to pay again",
              "do not pay again" in detail(v).lower(), detail(v))
        row = h.db.rows("payments")[0]
        check("an authorization is not written as paid", row.get("status") != "paid", row)
        check("an authorization records no paid_at", not row.get("paid_at"), row)
        check("an authorization does not approve the entry",
              h.db.rows("registrations")[0].get("status") == "pending",
              h.db.rows("registrations"))

        # Capture arrives later, by webhook, and settles it.
        fake.payments["pay_held"]["status"] = "captured"
        raw = webhook_event("payment.captured", o["orderId"], "pay_held", 50000)
        w = h.client.post("/api/payments/webhook", content=raw,
                          headers={"x-razorpay-signature": sign_webhook(raw),
                                   "content-type": "application/json"})
        check("capture settles what authorization did not", w.status_code == 200, detail(w))
        check("the entry is approved once the money is actually captured",
              h.db.rows("registrations")[0].get("status") == "approved",
              h.db.rows("registrations"))
    finally:
        teardown(h)


# ---------------------------------------------------------------------------
# A transient failure must be retried, not acknowledged
#
# The webhook is the only path that settles a payment when the browser never
# comes back. Answering 200 to a delivery we could not process told Razorpay
# the delivery had succeeded and threw away the last chance to record it.
# ---------------------------------------------------------------------------

def test_a_transient_failure_asks_razorpay_to_retry():
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        o = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(o["orderId"], "pay_flaky")

        async def unavailable(payment_id):
            raise rzp.RazorpayError("upstream timed out", status=504)
        rzp.fetch_payment = unavailable

        raw = webhook_event("payment.captured", o["orderId"], "pay_flaky", 50000)
        w = h.client.post("/api/payments/webhook", content=raw,
                          headers={"x-razorpay-signature": sign_webhook(raw),
                                   "content-type": "application/json"})
        check("an unreachable Razorpay makes the webhook fail so it is redelivered",
              w.status_code >= 500, "%s %s" % (w.status_code, detail(w)))
        check("nothing is confirmed on a transient failure",
              h.db.rows("registrations")[0].get("status") == "pending",
              h.db.rows("registrations"))

        # The redelivery, once Razorpay is reachable again, settles it.
        rzp.fetch_payment = fake.fetch_payment
        w2 = h.client.post("/api/payments/webhook", content=raw,
                           headers={"x-razorpay-signature": sign_webhook(raw),
                                    "content-type": "application/json"})
        check("the redelivery settles the payment", w2.status_code == 200, detail(w2))
        check("the entry is confirmed by the retry",
              h.db.rows("registrations")[0].get("status") == "approved",
              h.db.rows("registrations"))
    finally:
        teardown(h)


def test_a_terminal_rejection_is_not_retried():
    """An amount mismatch will not change on redelivery; acknowledge it."""
    h, fake, org, player, tid = setup(entry_fee=500.0)
    try:
        reg = body(register(h, tid, player))
        o = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(o["orderId"], "pay_wrong_amount", amount=100)

        raw = webhook_event("payment.captured", o["orderId"], "pay_wrong_amount", 100)
        w = h.client.post("/api/payments/webhook", content=raw,
                          headers={"x-razorpay-signature": sign_webhook(raw),
                                   "content-type": "application/json"})
        check("a wrong amount is acknowledged rather than retried forever",
              w.status_code == 200, detail(w))
        check("a wrong amount confirms nothing",
              h.db.rows("registrations")[0].get("status") == "pending",
              h.db.rows("registrations"))
    finally:
        teardown(h)


# ---------------------------------------------------------------------------
# Repairing a half-settled payment
# ---------------------------------------------------------------------------

def test_a_redelivery_repairs_an_unconfirmed_entry():
    """
    The payment row settled but the registration write did not.

    Settling is two writes and the second can fail alone. The early return for
    an already-paid row used to skip the repair, so every later redelivery came
    back before reaching it and the entry stayed unpaid for good.
    """
    h, fake, org, player, tid = setup()
    try:
        reg = body(register(h, tid, player))
        o = body(h.post("/api/payments/registrations/%s/order" % reg["id"], {}, user_id=player))
        fake.pay(o["orderId"], "pay_halfway")
        h.post("/api/payments/verify", {
            "razorpay_order_id": o["orderId"],
            "razorpay_payment_id": "pay_halfway",
            "razorpay_signature": sign_callback(o["orderId"], "pay_halfway"),
        }, user_id=player)

        # Simulate the registration write having been lost.
        for row in h.db.tables["registrations"]:
            row["status"] = "pending"
            row["payment_status"] = "pending"

        raw = webhook_event("payment.captured", o["orderId"], "pay_halfway", 50000)
        w = h.client.post("/api/payments/webhook", content=raw,
                          headers={"x-razorpay-signature": sign_webhook(raw),
                                   "content-type": "application/json"})

        check("the redelivery is accepted", w.status_code == 200, detail(w))
        r = h.db.rows("registrations")[0]
        check("a redelivery repairs an entry whose confirmation was lost",
              r.get("status") == "approved" and r.get("payment_status") == "paid", r)
    finally:
        teardown(h)


def test_a_malformed_signature_is_rejected_not_raised():
    """
    Every shape of rubbish a caller can put in the signature header.

    `hmac.compare_digest` on two `str` values raises TypeError the moment
    either holds a non-ASCII character, and the signature is entirely
    attacker-supplied -- Starlette decodes request headers as latin-1, so a
    webhook delivery carrying an accented byte in x-razorpay-signature turned
    an unauthenticated rejection into an unhandled 500. Razorpay reads a 5xx as
    a failed delivery and retries it, so one malformed header became a retry
    loop against an endpoint that was never going to accept it.

    A genuine signature is lowercase hex. Anything that is not simply does not
    match.
    """
    original_webhook = rzp.settings.RAZORPAY_WEBHOOK_SECRET
    original_id = rzp.settings.RAZORPAY_KEY_ID
    original_secret = rzp.settings.RAZORPAY_KEY_SECRET
    rzp.settings.RAZORPAY_WEBHOOK_SECRET = "whsec_offline"
    rzp.settings.RAZORPAY_KEY_ID = KEY_ID
    rzp.settings.RAZORPAY_KEY_SECRET = KEY_SECRET
    try:
        body = b'{"event":"payment.captured"}'
        rubbish = [
            ("an accented character", "é"),
            ("an emoji", "😀"),
            ("a non-ASCII digest-length string", "é" * 64),
            ("an empty string", ""),
            ("None", None),
            ("bytes rather than str", b"deadbeef"),
            ("plain wrong hex", "deadbeef"),
        ]
        for label, sig in rubbish:
            try:
                out = rzp.verify_webhook_signature(body, sig)
                check("a webhook signature that is %s is refused, not raised" % label,
                      out is False, "%s -> %r" % (label, out))
            except Exception as e:
                check("a webhook signature that is %s is refused, not raised" % label,
                      False, "%s RAISED %s" % (label, type(e).__name__))

            try:
                out = rzp.verify_payment_signature("order_1", "pay_1", sig)
                check("a payment signature that is %s is refused, not raised" % label,
                      out is False, "%s -> %r" % (label, out))
            except Exception as e:
                check("a payment signature that is %s is refused, not raised" % label,
                      False, "%s RAISED %s" % (label, type(e).__name__))

        # The good path still verifies, so the hardening did not break it.
        import hashlib as _h, hmac as _hm
        good = _hm.new(b"whsec_offline", body, _h.sha256).hexdigest()
        check("a correct webhook signature still verifies",
              rzp.verify_webhook_signature(body, good) is True, good[:16])
    finally:
        rzp.settings.RAZORPAY_WEBHOOK_SECRET = original_webhook
        rzp.settings.RAZORPAY_KEY_ID = original_id
        rzp.settings.RAZORPAY_KEY_SECRET = original_secret



SUITES = [
    ("pay then confirmed", test_pay_then_confirmed),
    ("free tournament", test_free_tournament_needs_no_payment),
    ("admin entry is waived", test_admin_entry_is_waived),
    ("forged signature", test_forged_signature_is_refused),
    ("amount mismatch", test_amount_mismatch_is_refused),
    ("payment for another order", test_payment_for_another_order_is_refused),
    ("failed payment", test_failed_payment_is_refused),
    ("unknown order", test_verify_for_an_order_we_never_opened),
    ("only the entrant may pay", test_only_the_entrant_may_pay),
    ("organiser may take payment", test_the_organiser_may_take_a_payment),
    ("verify twice", test_verify_twice_confirms_once),
    ("reopening an order", test_reopening_an_order_reuses_it),
    ("paid entry cannot reorder", test_paid_entry_cannot_be_ordered_again),
    ("webhook confirms", test_webhook_confirms_without_the_browser),
    ("webhook signature required", test_webhook_signature_is_required),
    ("webhook raw body", test_webhook_body_must_not_be_reserialised),
    ("webhook replay", test_webhook_replay_confirms_once),
    ("callback and webhook", test_callback_and_webhook_together_confirm_once),
    ("webhook failure", test_webhook_records_a_failure),
    ("webhook unknown order", test_webhook_for_an_unknown_order_is_acknowledged),
    ("webhook without a secret", test_webhook_refuses_when_no_secret_is_configured),
    ("unconfigured server", test_unconfigured_server_says_so),
    ("malformed signature", test_a_malformed_signature_is_rejected_not_raised),
    ("half-configured server", test_half_configured_server_is_treated_as_off),
    ("mode reporting", test_mode_is_reported),
    ("rupees to paise", test_rupees_to_paise),
    ("fee change", test_fee_change_does_not_move_an_existing_entry),
    ("without migration 015", test_entry_without_the_migration_falls_back_to_the_current_fee),
    ("second order cannot double charge", test_a_second_order_cannot_take_a_second_payment),
    ("paid entry cannot reorder (pre-checkout)", test_a_paid_entry_cannot_open_another_order),
    ("late failure does not erase success", test_a_late_failure_webhook_does_not_erase_a_settled_payment),
    ("failure for another attempt ignored", test_a_failure_for_a_different_attempt_is_ignored),
    ("paying does not reinstate a rejection", test_paying_does_not_reinstate_a_rejected_entry),
    ("cancelled tournament collects nothing", test_a_cancelled_tournament_collects_nothing),
    ("authorized is not captured", test_an_authorized_payment_does_not_confirm_the_entry),
    ("transient failure is retried", test_a_transient_failure_asks_razorpay_to_retry),
    ("terminal rejection is not retried", test_a_terminal_rejection_is_not_retried),
    ("redelivery repairs a half-settle", test_a_redelivery_repairs_an_unconfirmed_entry),
]


def main():
    for name, fn in SUITES:
        try:
            fn()
        except Exception:
            check("the %s suite runs to completion" % name, False,
                  traceback.format_exc()[-400:])

    total = sum(v[1] for v in RESULTS.values())
    failed = [(k, v) for k, v in sorted(RESULTS.items()) if v[0]]

    print("=" * 78)
    print("payments suite (real app, in-memory database, Razorpay stubbed)")
    print("=" * 78)
    print("assertions executed : %d" % total)
    print("invariants checked  : %d" % len(RESULTS))
    print("invariants violated : %d" % len(failed))
    print()
    if failed:
        print("FAILURES")
        print("-" * 78)
        for label, slot in failed:
            bad, ran, examples = slot
            print("  %s" % label)
            print("     %d of %d cases failed" % (bad, ran))
            for ex in examples:
                print("     e.g. %s" % ex)
            print()
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
