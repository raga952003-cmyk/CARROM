"""Conservative receipt triage: visual reuse and optional machine text reading.

Neither result proves that a bank transfer arrived. The receiver's transaction
history remains the authority for approving an entry.
"""
import base64
import io
import logging
import os
import re
import warnings
from decimal import Decimal, InvalidOperation

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import HTTPException


logger = logging.getLogger("uvicorn.error")
MAX_RECEIPT_PIXELS = 12_000_000
VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
_UPI_ID = re.compile(r"(?<![\w@])([A-Za-z0-9._-]{2,100}@[A-Za-z0-9.-]{2,100})(?![\w@])")
_UPI_PHONE = re.compile(r"(?<!\d)([6-9][0-9]{9})(?!\d)")
_DESTINATION_LABEL = re.compile(
    r"^\s*(?:(?:paid|sent|transferred)\s+to|payee|recipient|receiver|beneficiary|to)\b"
    r"(?:\s+(?:upi\s*id|vpa|mobile(?:\s+(?:no|number))?|phone(?:\s+(?:no|number))?))?"
    r"\s*[:\-–]?\s*(.*)$", re.I,
)
_OTHER_FIELD = re.compile(
    r"^\s*(?:from|paid\s+by|sender|payer|transaction|txn|utr|reference|amount|date|time|status)\b",
    re.I,
)


def image_dhash(content: bytes, mime_type: str) -> str | None:
    """A 64-bit difference hash robust to ordinary resize and recompression.

    This is a candidate finder, not proof of fraud. Two receipts with the same
    app template can be visually close despite representing different payments.
    """
    if mime_type == "application/pdf":
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as source:
                if source.width * source.height > MAX_RECEIPT_PIXELS:
                    raise HTTPException(status_code=413, detail="Receipt image has too many pixels.")
                source.load()
                image = ImageOps.exif_transpose(source).convert("L")
                pixels = list(image.resize((9, 8), Image.Resampling.BILINEAR).getdata())
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning,
            Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=422, detail="The receipt image could not be opened safely.") from exc
    bits = 0
    for row in range(8):
        for column in range(8):
            bits = (bits << 1) | (pixels[row * 9 + column + 1] > pixels[row * 9 + column])
    return f"{bits:016x}"


def _amounts_from_text(text: str) -> list[int]:
    amounts = []
    for found in re.finditer(r"(?:₹|INR|Rs\.?)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", text, re.I):
        try:
            paise = int(Decimal(found.group(1).replace(",", "")) * 100)
            if 0 < paise <= 100_000_000:
                amounts.append(paise)
        except (InvalidOperation, ValueError):
            continue
    return list(dict.fromkeys(amounts))


def _destination_identifier(text: str, expected_payee: str) -> tuple[str | None, bool | None]:
    """Compare only an explicitly labelled destination of the same ID type.

    A receipt can contain the sender's VPA, a support address, and masked
    numbers. None of those is evidence that the money went to another payee.
    Ambiguous or unreadable destinations are left for bank-credit review.
    """
    expected = (expected_payee or "").strip().lower()
    pattern = _UPI_ID if "@" in expected else _UPI_PHONE
    if not expected or ("@" in expected and not _UPI_ID.fullmatch(expected)) or (
        "@" not in expected and not _UPI_PHONE.fullmatch(expected)
    ):
        return None, None

    lines = text.splitlines()
    candidates: set[str] = set()
    for index, line in enumerate(lines):
        label = _DESTINATION_LABEL.match(line)
        if not label:
            continue
        fragments = [label.group(1)]
        if index + 1 < len(lines) and not _OTHER_FIELD.match(lines[index + 1]):
            fragments.append(lines[index + 1])
        for fragment in fragments:
            candidates.update(match.group(1).lower() for match in pattern.finditer(fragment))

    if len(candidates) != 1:
        return None, None
    found = next(iter(candidates))
    return found, found == expected


def compare_receipt_text(text: str, reference: str, expected_paise: int,
                         expected_payee: str = "") -> dict:
    """Only label a mismatch when the OCR found an unambiguous value."""
    normalized = re.sub(r"[^A-Za-z0-9]", "", text).upper()
    visible_reference = reference in normalized
    # UPI apps vary their label. A labeled reference is useful for an admin to
    # inspect, but an unlabeled alphanumeric run must not cause a rejection.
    labeled = re.search(
        r"(?:(?:UPI\s*(?:transaction|txn|ref(?:erence)?)\s*(?:id|no)?)|"
        r"(?:transaction\s*(?:id|no))|(?:UTR\s*(?:no)?)|"
        r"(?:reference\s*(?:id|no)?))"
        r"\s*[:#-]?\s*([A-Z0-9][A-Z0-9/-]{5,79})",
        text, re.I,
    )
    detected = None
    if labeled:
        candidate = re.sub(r"[^A-Za-z0-9]", "", labeled.group(1)).upper()
        detected = candidate[:80] if 6 <= len(candidate) <= 80 else None
    amounts = _amounts_from_text(text)
    amount_match = True if expected_paise in amounts else (False if len(amounts) == 1 else None)
    payee_read, payee_matches = _destination_identifier(text, expected_payee)
    return {
        "referenceRead": reference if visible_reference else detected,
        "referenceMatches": True if visible_reference else (False if detected else None),
        "amountPaiseRead": amounts[0] if len(amounts) == 1 else None,
        "amountMatches": amount_match,
        "payeeRead": payee_read,
        "payeeMatches": payee_matches,
    }


async def analyze_receipt_image(content: bytes, mime_type: str,
                                reference: str, expected_paise: int,
                                similar_count: int, expected_payee: str = "") -> dict:
    result = {"scanStatus": "not_configured", "similarImageCount": similar_count,
              "referenceRead": None, "referenceMatches": None,
              "amountPaiseRead": None, "amountMatches": None,
              "payeeRead": None, "payeeMatches": None}
    if mime_type == "application/pdf":
        result["scanStatus"] = "pdf_not_scanned"
        return result
    key = os.environ.get("PAYMENT_PROOF_VISION_API_KEY", "").strip()
    if not key:
        return result
    try:
        payload = {"requests": [{
            "image": {"content": base64.b64encode(content).decode("ascii")},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
        }]}
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(VISION_ENDPOINT, json=payload,
                                         headers={"X-Goog-Api-Key": key})
            response.raise_for_status()
        annotation = (response.json().get("responses") or [{}])[0]
        if annotation.get("error"):
            raise ValueError("Vision API returned an image error")
        text = ((annotation.get("fullTextAnnotation") or {}).get("text")
                or ((annotation.get("textAnnotations") or [{}])[0].get("description")) or "")
        if not text.strip():
            result["scanStatus"] = "unreadable"
            return result
        result.update(compare_receipt_text(text, reference, expected_paise,
                                           expected_payee))
        result["scanStatus"] = "scanned"
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        # A provider outage must never mark an entry paid or silently approve
        # a screenshot. Keep the pending proof for manual bank verification.
        logger.warning("Receipt text reading unavailable; proof remains pending manual review")
        result["scanStatus"] = "unavailable"
    return result
