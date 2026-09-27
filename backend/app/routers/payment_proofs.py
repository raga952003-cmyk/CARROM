"""Private, reviewed evidence for payments made to a tournament's GPay account."""
import hashlib
import logging
import re
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.database import get_admin_db
from app.services.access_control import require_tournament_access
from app.services.audit_service import record_audit
from app.utils.security import get_user_profile, verify_admin
from app.utils.serializers import camelize

logger = logging.getLogger("uvicorn.error")
router = APIRouter(tags=["payment-proofs"])
BUCKET = "payment-proofs"
MAX_FILE_BYTES = 5 * 1024 * 1024


class ProofReview(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str = Field(min_length=5, max_length=500)
    confirmed_received: bool = False


def _file_kind(content: bytes) -> tuple[str, str]:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", "webp"
    if content.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    raise HTTPException(status_code=422, detail="Upload a JPEG, PNG, WebP, or PDF receipt.")


def _entry_and_tournament(db, registration_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    rows = db.table("registrations").select("*").eq("id", registration_id).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Registration not found.")
    entry = rows[0]
    tournaments = db.table("tournaments").select("*").eq("id", entry["tournament_id"]).execute().data or []
    if not tournaments:
        raise HTTPException(status_code=404, detail="Tournament not found.")
    return entry, tournaments[0]


def _authorise_entry(db, entry: Dict[str, Any], profile: Dict[str, Any]) -> None:
    if str(entry.get("player_id") or "") == str(profile.get("id")):
        return
    team_id = entry.get("team_id")
    if team_id:
        teams = db.table("teams").select("player1_id, player2_id").eq("id", team_id).execute().data or []
        if teams and str(profile.get("id")) in (str(teams[0].get("player1_id")), str(teams[0].get("player2_id"))):
            return
    if profile.get("role") == "admin":
        require_tournament_access(db, entry["tournament_id"], profile, "payment.proof")
        return
    raise HTTPException(status_code=403, detail="This payment proof does not belong to your entry.")


def _signed_proof(db, row: Dict[str, Any]) -> Dict[str, Any]:
    proof = camelize(row)
    try:
        signed = db.storage.from_(BUCKET).create_signed_url(row["object_path"], 300)
        proof["fileUrl"] = signed.get("signedURL") or signed.get("signedUrl")
    except Exception:
        proof["fileUrl"] = None
    return proof


@router.post("/registrations/{registration_id}/payment-proof")
async def submit_payment_proof(
    registration_id: str,
    transaction_reference: str = Form(...),
    file: UploadFile = File(...),
    profile=Depends(get_user_profile),
):
    db = get_admin_db()
    entry, tournament = _entry_and_tournament(db, registration_id)
    _authorise_entry(db, entry, profile)
    if entry.get("payment_status") != "pending" or entry.get("status") == "rejected":
        raise HTTPException(status_code=409, detail="This entry cannot accept a payment proof.")
    if tournament.get("status") in ("completed", "cancelled"):
        raise HTTPException(status_code=409, detail="This tournament no longer accepts payments.")
    if not tournament.get("gpay_upi_id"):
        raise HTTPException(status_code=409, detail="GPay payment is not offered for this tournament.")
    fee = entry.get("fee_paise")
    if fee is None:
        fee = round(float(tournament.get("entry_fee") or 0) * 100)
    if int(fee) <= 0:
        raise HTTPException(status_code=409, detail="This entry has no fee to pay.")

    reference = re.sub(r"[^A-Za-z0-9]", "", transaction_reference or "").upper()
    if not 6 <= len(reference) <= 80:
        raise HTTPException(status_code=422, detail="Enter the 6 to 80 character UPI transaction reference.")
    content = await file.read(MAX_FILE_BYTES + 1)
    if not content or len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Payment proof must be a nonempty file of at most 5 MB.")
    mime, extension = _file_kind(content)
    digest = hashlib.sha256(content).hexdigest()
    for column, value in (("transaction_reference", reference), ("content_sha256", digest)):
        used = db.table("payment_proofs").select("id").eq(column, value).limit(1).execute().data or []
        if used:
            raise HTTPException(status_code=409, detail="This transaction reference or file was already submitted.")
    # Check normalized desk references before storing a file. The review RPC
    # repeats this in its own transaction to close any upload/review race.
    claimed = db.rpc("payment_reference_claimed", {
        "p_reference": reference,
    }).execute().data
    if claimed is True:
        raise HTTPException(status_code=409, detail="This transaction reference is already recorded as a payment.")
    pending = db.table("payment_proofs").select("id").eq(
        "registration_id", registration_id).eq("status", "pending").limit(1).execute().data or []
    if pending:
        raise HTTPException(status_code=409, detail="A payment proof is already awaiting review for this entry.")

    proof_id = str(uuid.uuid4())
    path = f"{entry['tournament_id']}/{registration_id}/{proof_id}.{extension}"
    try:
        db.storage.from_(BUCKET).upload(path, content, {"content-type": mime, "upsert": "false"})
    except Exception as exc:
        logger.error("Payment proof storage upload failed: %s", exc)
        raise HTTPException(status_code=503, detail="Could not store the proof. Try again shortly.")

    row = {
        "id": proof_id, "registration_id": registration_id,
        "tournament_id": entry["tournament_id"], "submitted_by": profile["id"],
        "transaction_reference": reference, "content_sha256": digest,
        "payee_upi_id": tournament["gpay_upi_id"],
        "object_path": path, "mime_type": mime,
        "content_size_bytes": len(content), "amount_paise": int(fee),
    }
    try:
        saved = db.table("payment_proofs").insert(row).execute().data or []
        if not saved:
            raise RuntimeError("Proof record was not returned")
    except Exception as exc:
        try:
            db.storage.from_(BUCKET).remove([path])
        except Exception:
            logger.warning("Could not remove orphaned payment proof %s", path)
        if "23505" in str(exc) or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="This transaction reference or file was already submitted.")
        logger.error("Payment proof record failed: %s", exc)
        raise HTTPException(status_code=503, detail="Could not record the proof. Try again shortly.")
    record_audit(db, actor=profile, action="payment.proof_submitted",
                 entity_type="registration", entity_id=registration_id,
                 new_state={"proof_id": proof_id, "transaction_reference": reference})
    return {"status": "pending_review", "proof": _signed_proof(db, saved[0]),
            "message": "Proof submitted. Your entry is not paid until an organizer checks the received transaction."}


@router.get("/registrations/{registration_id}/payment-proofs")
async def list_entry_proofs(registration_id: str, profile=Depends(get_user_profile)):
    db = get_admin_db()
    entry, _ = _entry_and_tournament(db, registration_id)
    _authorise_entry(db, entry, profile)
    rows = db.table("payment_proofs").select("*").eq(
        "registration_id", registration_id).order("submitted_at", desc=True).execute().data or []
    return [_signed_proof(db, row) for row in rows]


@router.get("/tournaments/{tournament_id}/payment-proofs")
async def list_tournament_proofs(tournament_id: str, admin=Depends(verify_admin)):
    db = get_admin_db()
    require_tournament_access(db, tournament_id, admin, "payment.proof_review")
    rows = db.table("payment_proofs").select("*").eq(
        "tournament_id", tournament_id).order("submitted_at", desc=True).execute().data or []
    return [_signed_proof(db, row) for row in rows]


@router.post("/payment-proofs/{proof_id}/review")
async def review_payment_proof(proof_id: str, body: ProofReview, admin=Depends(verify_admin)):
    if body.decision == "approved" and not body.confirmed_received:
        raise HTTPException(status_code=422, detail="Confirm the transaction is present in the receiving GPay account before approval.")
    db = get_admin_db()
    rows = db.table("payment_proofs").select("*").eq("id", proof_id).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Payment proof not found.")
    proof = rows[0]
    require_tournament_access(db, proof["tournament_id"], admin, "payment.proof_review")
    result = db.rpc("review_payment_proof", {
        "p_proof_id": proof_id, "p_reviewer_id": admin["id"],
        "p_decision": body.decision, "p_note": body.note,
    }).execute().data
    # The RPC writes its audit row in the same transaction as the review.
    return camelize(result or {})
