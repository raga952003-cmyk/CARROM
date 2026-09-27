"""GPay proof HTTP flow against in-memory Supabase and private storage fakes.

No external Storage, UPI account, or payment provider is contacted.
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness  # noqa: E402


RESULTS = {}
PNG = b"\x89PNG\r\n\x1a\n" + b"proof-image-bytes"
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


def submit(h, user_id, reference, content=PNG):
    return h.client.post(
        "/api/registrations/" + REG_ID + "/payment-proof",
        headers=h.auth(user_id),
        data={"transaction_reference": reference},
        files={"file": ("receipt.png", io.BytesIO(content), "image/png")},
    )


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
    original_rpc = h.db.rpc

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
        return original_rpc(name, params)

    h.db.rpc = reference_rpc

    forbidden = submit(h, stranger, "123456789012")
    check("stranger cannot submit proof", forbidden.status_code == 403
          and not storage.uploads)

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
    original_rpc = h.db.rpc

    def fake_rpc(name, params=None):
        if name == "review_payment_proof":
            rpc_calls.append(params)
            return RpcResult({"proof": {"id": proof_id, "status": "approved"},
                              "registration": {"id": REG_ID, "payment_status": "paid"},
                              "payment": {"method": "gpay_upi"}})
        return original_rpc(name, params)

    h.db.rpc = fake_rpc
    owner_review = h.post(review_url, json={
        "decision": "approved", "note": "Bank credit checked",
        "confirmed_received": True,
    }, user_id=owner)
    check("owner dispatches verified review to atomic RPC",
          owner_review.status_code == 200 and len(rpc_calls) == 1
          and rpc_calls[0].get("p_proof_id") == proof_id
          and rpc_calls[0].get("p_reviewer_id") == owner)

    for label, passed in RESULTS.items():
        print(("PASS" if passed else "FAIL") + " payment proof: " + label)
    return 0 if all(RESULTS.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
