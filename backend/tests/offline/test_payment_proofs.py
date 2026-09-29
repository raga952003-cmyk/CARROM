"""GPay proof HTTP flow against in-memory Supabase and private storage fakes.

No external Storage, UPI account, or payment provider is contacted.
"""
import io
import os
import sys
import asyncio
import base64
from unittest.mock import patch
import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness  # noqa: E402
from fakedb import PostgrestError  # noqa: E402
from PIL import Image  # noqa: E402
from app.services.payment_image_analysis import (  # noqa: E402
    image_dhash, compare_receipt_text,
)
import app.services.payment_image_analysis as image_analysis  # noqa: E402
import app.services.razorpay_client as razorpay_client  # noqa: E402
from app.config import settings  # noqa: E402


RESULTS = {}
_image = Image.new("RGB", (32, 32), "white")
for _x in range(6, 25):
    for _y in range(8, 13):
        _image.putpixel((_x, _y), (25, 70, 130))
_buffer = io.BytesIO()
_image.save(_buffer, format="PNG")
PNG = _buffer.getvalue()
REG_ID = "55555555-5555-5555-5555-555555555555"


class Storage:
    def __init__(self):
        self.uploads = []
        self.removals = []
        self.signs = []

    def from_(self, bucket):
        assert bucket == "payment-proofs"
        return self

    def upload(self, path, content, options):
        self.uploads.append((path, content, options))
        return {"path": path}

    def remove(self, paths):
        self.removals.extend(paths)
        return paths

    def create_signed_url(self, path, seconds):
        self.signs.append((path, seconds))
        return {"signedURL": "https://signed.example.invalid/" + path}


class RpcResult:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


def check(label, condition):
    RESULTS[label] = bool(condition)


def submit(h, user_id, reference, content=PNG, registration_id=REG_ID):
    return h.client.post(
        "/api/registrations/" + registration_id + "/payment-proof",
        headers=h.auth(user_id),
        data={"transaction_reference": reference},
        files={"file": ("receipt.png", io.BytesIO(content), "image/png")},
    )


def test_razorpay_auth_failure_keeps_direct_upi_available():
    h = Harness()
    owner = h.make_user("Fallback Owner", role="admin")
    payer = h.make_user("Fallback Payer")
    tid = h.seed_tournament(owner, status="registration_open", entry_fee=100,
                            gpay_upi_id="club@okbank")
    h.db.seed("registrations", [{
        "id": REG_ID, "tournament_id": tid, "type": "singles",
        "player_id": payer, "team_id": None,
        "status": "pending", "payment_status": "pending", "fee_paise": 10000,
    }])
    h.db.storage = Storage()
    original_rpc = h.db.rpc

    def proof_rpc(name, params=None):
        if name == "payment_reference_claimed":
            return RpcResult(False)
        return original_rpc(name, params)

    h.db.rpc = proof_rpc

    async def reject_order(**_kwargs):
        raise razorpay_client.RazorpayError("Authentication failed", status=401)

    with patch.object(razorpay_client, "razorpay_configured", return_value=True), \
            patch.object(razorpay_client, "checkout_enabled", return_value=True), \
            patch.object(razorpay_client, "webhook_configured", return_value=True), \
            patch.object(razorpay_client, "create_order", reject_order):
        refused = h.post("/api/payments/registrations/%s/order" % REG_ID,
                         {}, user_id=payer)

    check("provider authentication failure creates no charge or order",
          refused.status_code == 502 and not h.db.rows("payments"))
    direct = submit(h, payer, "876543210987")
    check("player may submit UPI receipt after provider rejects order",
          direct.status_code == 200
          and direct.json().get("status") == "pending_review"
          and direct.json().get("proof", {}).get("payeeUpiId") == "club@okbank")
    check("UPI fallback waits for receiving-bank review",
          h.db.rows("registrations")[0]["payment_status"] == "pending"
          and h.db.rows("registrations")[0]["status"] == "pending")


def test_reconsideration():
    h = Harness()
    owner = h.make_user("Correction Owner", role="admin")
    outsider = h.make_user("Correction Outsider", role="admin")
    manager = h.make_user("Correction Manager", role="admin")
    payer = h.make_user("Correction Payer")
    tid = h.seed_tournament(owner, status="registration_open", entry_fee=100,
                            gpay_upi_id="9876543210")
    h.db.seed("registrations", [{
        "id": REG_ID, "tournament_id": tid, "type": "singles",
        "player_id": payer, "team_id": None,
        "status": "pending", "payment_status": "pending", "fee_paise": 10000,
    }])
    h.db.seed("tournament_access", [{
        "tournament_id": tid, "user_id": manager,
        "status": "approved", "access_role": "manager",
    }])
    h.db.storage = Storage()
    base_rpc = h.db.rpc

    def proof_rpc(name, params=None):
        if name == "payment_reference_claimed":
            return RpcResult(False)
        return base_rpc(name, params)

    h.db.rpc = proof_rpc
    submitted = submit(h, payer, "765432109876")
    proof_id = (submitted.json().get("proof") or {}).get("id") if submitted.status_code == 200 else None
    check("correction setup stores a pending proof", submitted.status_code == 200 and bool(proof_id))
    review_url = "/api/payment-proofs/" + str(proof_id) + "/review"
    rejected = h.post(review_url, json={
        "decision": "rejected", "note": "Credit not visible in receiving account",
    }, user_id=owner)
    check("original rejection is recorded", rejected.status_code == 200
          and rejected.json().get("proof", {}).get("status") == "rejected")
    duplicate = submit(h, payer, "765432109876", PNG + b"changed")
    check("same UTR remains reserved after rejection", duplicate.status_code == 409)

    note = "Credit arrived later; verified exact UTR and INR 100 in receiving account"
    missing_bank_confirmation = h.post(review_url, json={
        "decision": "approved", "note": note, "confirmed_received": False,
    }, user_id=owner)
    check("correction needs receiving-account confirmation", missing_bank_confirmation.status_code == 422)
    short_note = h.post(review_url, json={
        "decision": "approved", "note": "Credit found", "confirmed_received": True,
    }, user_id=owner)
    check("correction needs a detailed fresh note", short_note.status_code == 422)
    unauthorized = h.post(review_url, json={
        "decision": "approved", "note": note, "confirmed_received": True,
    }, user_id=outsider)
    check("unrelated admin cannot correct rejection", unauthorized.status_code == 403)
    player_attempt = h.post(review_url, json={
        "decision": "approved", "note": note, "confirmed_received": True,
    }, user_id=payer)
    check("player cannot correct rejection", player_attempt.status_code == 403)

    # A paid ledger row with the UTR, including a desk entry for another
    # registration, must block the correction without altering this proof.
    h.db.seed("payments", [{
        "id": "other-paid-credit", "registration_id": "other-entry",
        "tournament_id": tid, "status": "paid", "method": "upi",
        "notes": {"reference": "7654-3210-9876"},
    }])
    before_payments = len(h.db.rows("payments"))
    conflict = h.post(review_url, json={
        "decision": "approved", "note": note, "confirmed_received": True,
    }, user_id=manager)
    proof_row = next(p for p in h.db.rows("payment_proofs") if p["id"] == proof_id)
    entry_row = next(r for r in h.db.rows("registrations") if r["id"] == REG_ID)
    check("duplicate bank UTR blocks correction atomically", conflict.status_code == 409
          and len(h.db.rows("payments")) == before_payments
          and proof_row["status"] == "rejected" and entry_row["payment_status"] == "pending")
    h.db.tables["payments"] = [p for p in h.db.rows("payments")
                                if p.get("id") != "other-paid-credit"]

    corrected = h.post(review_url, json={
        "decision": "approved", "note": note, "confirmed_received": True,
    }, user_id=manager)
    corrected_body = corrected.json() if corrected.status_code == 200 else {}
    audit = [a for a in h.db.rows("audit_logs")
             if a.get("entity_id") == proof_id]
    check("approved manager can correct rejected proof", corrected.status_code == 200
          and corrected_body.get("proof", {}).get("status") == "approved"
          and corrected_body.get("registration", {}).get("paymentStatus") == "paid"
          and corrected_body.get("registration", {}).get("status") == "approved")
    check("correction creates one paid ledger row", len(h.db.rows("payments")) == 1
          and h.db.rows("payments")[0].get("method") == "gpay_upi")
    check("audit keeps rejection and correction with prior state", len(audit) == 2
          and audit[0]["action"] == "payment.proof_rejected"
          and audit[1]["action"] == "payment.proof_reconsidered_approved"
          and audit[1]["previous_state"]["review_note"] == "Credit not visible in receiving account")
    repeated = h.post(review_url, json={
        "decision": "approved", "note": note, "confirmed_received": True,
    }, user_id=manager)
    check("retry returns same correction without a second payment", repeated.status_code == 200
          and len(h.db.rows("payments")) == 1
          and len([a for a in h.db.rows("audit_logs") if a.get("entity_id") == proof_id]) == 2)


def main():
    RESULTS.clear()
    h = Harness()
    owner = h.make_user("Owner", role="admin")
    outsider_admin = h.make_user("Other Admin", role="admin")
    player = h.make_user("Paying Player")
    stranger = h.make_user("Stranger")
    tid = h.seed_tournament(
        owner, status="registration_open", entry_fee=100,
        gpay_upi_id="9876543210",
    )
    h.db.seed("registrations", [{
        "id": REG_ID, "tournament_id": tid, "type": "singles",
        "player_id": player, "team_id": None,
        "status": "pending", "payment_status": "pending",
        "fee_paise": 10000,
    }])
    storage = Storage()
    h.db.storage = storage
    base_rpc = h.db.rpc

    def reference_rpc(name, params=None):
        if name == "payment_reference_claimed":
            reference = (params or {}).get("p_reference", "")
            normalized = "".join(ch for ch in reference.upper() if ch.isalnum())
            used = any(
                "".join(ch for ch in (payment.get("notes") or {}).get("reference", "").upper()
                        if ch.isalnum()) == normalized
                for payment in h.db.rows("payments")
                if payment.get("method") in ("upi", "bank_transfer", "gpay_upi")
                and payment.get("status") in ("paid", "refunded", "refund_due")
            )
            return RpcResult(used)
        return base_rpc(name, params)

    h.db.rpc = reference_rpc

    for unsafe_payee in (None, "not-an-upi"):
        h.db.tables["tournaments"][0]["gpay_upi_id"] = unsafe_payee
        unavailable = submit(h, player, "123456789012")
        check("no proof is accepted with unconfigured payee %r" % unsafe_payee,
              unavailable.status_code == 409 and not storage.uploads)
    h.db.tables["tournaments"][0]["gpay_upi_id"] = "9876543210"

    forbidden = submit(h, stranger, "123456789012")
    check("stranger cannot submit proof", forbidden.status_code == 403
          and not storage.uploads)

    pdf = submit(h, player, "123456789012", b"%PDF-1.4\n1 0 obj\n")
    check("PDF cannot bypass receipt image checks",
          pdf.status_code == 422 and not storage.uploads)

    for status in ("paid", "refund_due"):
        h.db.seed("payments", [{
            "id": "earlier-" + status, "registration_id": REG_ID,
            "tournament_id": tid, "status": status, "method": "upi",
        }])
        already_collected = submit(h, player, "123456789012")
        check("ledger %s blocks second UPI proof" % status,
              already_collected.status_code == 409 and not storage.uploads)
        h.db.tables["payments"] = [row for row in h.db.rows("payments")
                                   if row.get("id") != "earlier-" + status]

    accepted = submit(h, player, "1234 5678 9012")
    body = accepted.json() if accepted.status_code == 200 else {}
    proof = body.get("proof", {})
    proof_id = proof.get("id")
    check("member submits pending proof", accepted.status_code == 200
          and body.get("status") == "pending_review"
          and proof.get("transactionReference") == "123456789012"
          and proof.get("payeeUpiId") == "9876543210")
    check("server stored normalized receipt privately", len(storage.uploads) == 1
          and storage.uploads[0][0].startswith(tid + "/" + REG_ID + "/")
          and storage.uploads[0][2].get("upsert") == "false")
    check("submitter receives short-lived signed URL",
          proof.get("fileUrl", "").startswith("https://signed.example.invalid/")
          and storage.signs[-1][1] == 300)

    duplicate_reference = submit(h, player, "123456789012", PNG + b"changed")
    duplicate_file = submit(h, player, "999999999999", PNG)
    check("reused transaction reference rejected", duplicate_reference.status_code == 409)
    check("reused file hash rejected", duplicate_file.status_code == 409)
    check("duplicates never reach Storage", len(storage.uploads) == 1)

    h.db.seed("payments", [{
        "id": "paid-at-desk", "registration_id": "another-entry",
        "tournament_id": tid, "status": "paid", "method": "upi",
        "notes": {"reference": "AA-1234 56"},
    }])
    desk_reference = submit(h, player, "AA123456", PNG + b"new")
    check("desk-recorded reference is rejected after normalization",
          desk_reference.status_code == 409 and len(storage.uploads) == 1)
    manual_same_reference = h.post("/api/registrations/" + REG_ID + "/manual-payment", {
        "method": "upi", "reference": "1234-5678-9012",
    }, user_id=owner)
    check("a proof reference cannot also be recorded as desk payment",
          manual_same_reference.status_code == 409)

    before_signs = len(storage.signs)
    stranger_list = h.get("/api/registrations/" + REG_ID + "/payment-proofs", stranger)
    check("stranger cannot obtain signed proof URL", stranger_list.status_code == 403
          and len(storage.signs) == before_signs)
    owner_list = h.get("/api/tournaments/" + tid + "/payment-proofs", owner)
    outsider_list = h.get("/api/tournaments/" + tid + "/payment-proofs", outsider_admin)
    check("owner may inspect private review queue", owner_list.status_code == 200
          and len(owner_list.json()) == 1
          and owner_list.json()[0]["fileUrl"].startswith("https://signed.example.invalid/"))
    check("unrelated admin cannot inspect review queue", outsider_list.status_code == 403)

    review_url = "/api/payment-proofs/" + str(proof_id) + "/review"
    missing_confirmation = h.post(review_url, json={
        "decision": "approved", "note": "Bank credit checked",
        "confirmed_received": False,
    }, user_id=owner)
    check("approval requires explicit received-payment confirmation",
          missing_confirmation.status_code == 422)
    outsider_review = h.post(review_url, json={
        "decision": "approved", "note": "Bank credit checked",
        "confirmed_received": True,
    }, user_id=outsider_admin)
    check("unrelated admin cannot approve", outsider_review.status_code == 403)

    rpc_calls = []
    review_fallback_rpc = h.db.rpc

    def fake_rpc(name, params=None):
        if name == "review_payment_proof_v2":
            rpc_calls.append(params)
            return RpcResult({"proof": {"id": proof_id, "status": "approved"},
                              "registration": {"id": REG_ID, "payment_status": "paid",
                                               "status": "approved"},
                              "payment": {"method": "gpay_upi"}})
        return review_fallback_rpc(name, params)

    h.db.rpc = fake_rpc
    owner_review = h.post(review_url, json={
        "decision": "approved", "note": "Bank credit checked",
        "confirmed_received": True,
    }, user_id=owner)
    check("owner dispatches verified review to atomic RPC",
          owner_review.status_code == 200 and len(rpc_calls) == 1
          and rpc_calls[0].get("p_proof_id") == proof_id
          and rpc_calls[0].get("p_reviewer_id") == owner
          and rpc_calls[0].get("p_allow_any_admin") is False)

    with patch.object(settings, "ENFORCE_TOURNAMENT_OWNERSHIP", False):
        open_review = h.post(review_url, json={
            "decision": "approved", "note": "Bank credit checked",
            "confirmed_received": True,
        }, user_id=outsider_admin)
    check("single-operator mode allows another admin to review securely",
          open_review.status_code == 200 and len(rpc_calls) == 2
          and rpc_calls[-1].get("p_reviewer_id") == outsider_admin
          and rpc_calls[-1].get("p_allow_any_admin") is True)

    tournament_row = next(row for row in h.db.tables["tournaments"] if row["id"] == tid)
    saved_owner = tournament_row.get("owner_id")
    tournament_row["owner_id"] = None
    try:
        legacy_review = h.post(review_url, json={
            "decision": "approved", "note": "Bank credit checked",
            "confirmed_received": True,
        }, user_id=outsider_admin)
    finally:
        tournament_row["owner_id"] = saved_owner
    check("unowned legacy tournament allows admin review with strict setting",
          legacy_review.status_code == 200 and len(rpc_calls) == 3
          and rpc_calls[-1].get("p_reviewer_id") == outsider_admin
          and rpc_calls[-1].get("p_allow_any_admin") is False)

    def missing_review_rpc(name, params=None):
        if name == "review_payment_proof_v2":
            raise PostgrestError("Could not find the function public.review_payment_proof_v2", "PGRST202")
        return fake_rpc(name, params)

    h.db.rpc = missing_review_rpc
    missing_migration = h.post(review_url, json={
        "decision": "approved", "note": "Bank credit checked",
        "confirmed_received": True,
    }, user_id=owner)
    h.db.rpc = fake_rpc
    check("missing review migration gives a clear unavailable response",
          missing_migration.status_code == 503
          and "migration 025" in missing_migration.json().get("detail", ""))

    # A screenshot re-encoded as JPEG has different bytes and a different
    # typed UTR, but should still be surfaced as possible reuse for review.
    second_player = h.make_user("Another Paying Player")
    second_reg = "66666666-6666-6666-6666-666666666666"
    h.db.seed("registrations", [{
        "id": second_reg, "tournament_id": tid, "type": "singles",
        "player_id": second_player, "status": "pending",
        "payment_status": "pending", "fee_paise": 10000,
    }])
    jpeg_buffer = io.BytesIO()
    _image.save(jpeg_buffer, format="JPEG", quality=85)
    jpeg = jpeg_buffer.getvalue()
    check("re-encoding changes the exact checksum but keeps a close visual hash",
          jpeg != PNG and (int(image_dhash(jpeg, "image/jpeg"), 16)
                           ^ int(image_dhash(PNG, "image/png"), 16)).bit_count() <= 4)
    reused = submit(h, second_player, "222222222222", jpeg, second_reg)
    reused_body = reused.json() if reused.status_code == 200 else {}
    check("another entry with a changed UTR gets a visual reuse warning",
          reused.status_code == 200
          and (reused_body.get("proof", {}).get("imageAnalysis") or {}).get("similarImageCount", 0) >= 1
          and reused_body.get("status") == "pending_review")
    check("visual warning never auto-approves an entry",
          any(row["id"] == second_reg and row.get("payment_status") == "pending"
              for row in h.db.rows("registrations")))
    reused_id = (reused_body.get("proof") or {}).get("id")
    before_rpcs = len(rpc_calls)
    short_warning_review = h.post("/api/payment-proofs/" + str(reused_id) + "/review", json={
        "decision": "approved", "note": "checked",
        "confirmed_received": True,
    }, user_id=owner)
    check("similar image requires a detailed verified-credit note",
          short_warning_review.status_code == 422 and len(rpc_calls) == before_rpcs)
    reused_row = next(row for row in h.db.tables["payment_proofs"]
                      if row["id"] == reused_id)
    reused_row["image_analysis"]["payeeMatches"] = False
    wrong_payee_review = h.post("/api/payment-proofs/" + str(reused_id) + "/review", json={
        "decision": "approved", "note": "I reviewed the credited account and amount",
        "confirmed_received": True,
    }, user_id=owner)
    check("OCR evidence of another payee blocks approval before RPC",
          wrong_payee_review.status_code == 409 and len(rpc_calls) == before_rpcs)

    matching = compare_receipt_text("UPI transaction ID: 999888777666\nPaid ₹ 100.00",
                                    "999888777666", 10000)
    mismatch = compare_receipt_text("UTR: 999888777666\nINR 90.00",
                                    "222222222222", 10000)
    check("receipt text identifies matching reference and fee",
          matching["referenceMatches"] is True and matching["amountMatches"] is True)
    check("receipt text flags a different reference and amount",
          mismatch["referenceMatches"] is False and mismatch["amountMatches"] is False)
    correct_payee = compare_receipt_text(
        "Paid by: sender@okbank\nPaid to: club@okbank\n"
        "UPI transaction ID: 999888777666\nPaid ₹ 100.00",
        "999888777666", 10000, "club@okbank",
    )
    other_payee = compare_receipt_text(
        "Paid by: sender@okbank\nRecipient UPI ID: other@okaxis\n"
        "UPI transaction ID: 999888777666\nPaid ₹ 100.00",
        "999888777666", 10000, "club@okbank",
    )
    unreadable_payee = compare_receipt_text(
        "Paid by sender@okbank\nPaid to: C*** Club\nINR 100.00",
        "999888777666", 10000, "club@okbank",
    )
    phone_payee = compare_receipt_text(
        "Sent to: 9876543210\nINR 100.00", "999888777666", 10000,
        "9876543210",
    )
    wrong_phone = compare_receipt_text(
        "Sent to: 9876543211\nINR 100.00", "999888777666", 10000,
        "9876543210",
    )
    check("OCR compares the labelled recipient, not the sender",
          correct_payee["payeeMatches"] is True
          and correct_payee["payeeRead"] == "club@okbank")
    check("same amount sent to another VPA is identified",
          other_payee["payeeMatches"] is False
          and other_payee["amountMatches"] is True)
    check("masked or absent recipient stays unknown for bank review",
          unreadable_payee["payeeMatches"] is None)
    check("recipient phone matches or flags a different phone",
          phone_payee["payeeMatches"] is True
          and wrong_phone["payeeMatches"] is False)

    calls = []

    class VisionResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"responses": [{"fullTextAnnotation": {
                "text": "Paid to: club@okbank\nUPI transaction ID: 999888777666\nPaid ₹ 100.00",
            }}]}

    class VisionClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return VisionResponse()

    with patch.dict(os.environ, {"PAYMENT_PROOF_VISION_API_KEY": "test-only-key"}), \
            patch.object(image_analysis.httpx, "AsyncClient", VisionClient):
        scanned = asyncio.run(image_analysis.analyze_receipt_image(
            PNG, "image/png", "999888777666", 10000, 0, "club@okbank"))
    check("configured OCR compares image text without approving payment",
          scanned["scanStatus"] == "scanned"
          and scanned["referenceMatches"] is True
          and scanned["amountMatches"] is True
          and scanned["payeeMatches"] is True)
    check("OCR sends only the receipt to the authorized server endpoint",
          len(calls) == 1
          and calls[0][0] == image_analysis.VISION_ENDPOINT
          and calls[0][1]["headers"]["X-Goog-Api-Key"] == "test-only-key"
          and base64.b64decode(calls[0][1]["json"]["requests"][0]["image"]["content"]) == PNG)

    class FailingVisionClient(VisionClient):
        async def post(self, url, **kwargs):
            raise httpx.ConnectError("simulated provider outage")

    with patch.dict(os.environ, {"PAYMENT_PROOF_VISION_API_KEY": "test-only-key"}), \
            patch.object(image_analysis.httpx, "AsyncClient", FailingVisionClient):
        unavailable = asyncio.run(image_analysis.analyze_receipt_image(
            PNG, "image/png", "999888777666", 10000, 0))
    check("OCR outage leaves receipt for manual review",
          unavailable["scanStatus"] == "unavailable"
          and unavailable["referenceMatches"] is None)

    test_reconsideration()
    test_razorpay_auth_failure_keeps_direct_upi_available()

    for label, passed in RESULTS.items():
        print(("PASS" if passed else "FAIL") + " payment proof: " + label)
    return 0 if all(RESULTS.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
