"""
Tournament ownership and delegated access.

Every account with role='admin' could previously edit, re-draw or delete any
tournament, including ones someone else created. A tournament now has an owner,
and any other admin who needs to help run it requests access that the owner
approves or rejects.

Roles a request can carry:
  manager  full control of that tournament
  scorer   may run matches and enter scores, but not re-draw fixtures,
           change settings, or delete the tournament

Requires migration 003. Until that is applied the ownership columns do not
exist, so enforcement reports itself as inactive rather than silently blocking
or silently allowing without saying which.
"""
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
import logging

from app.config import settings

logger = logging.getLogger("uvicorn.error")

if not settings.ENFORCE_TOURNAMENT_OWNERSHIP:
    logger.warning(
        "Tournament ownership is not enforced: any admin account can manage, "
        "score and delete every tournament. Set ENFORCE_TOURNAMENT_OWNERSHIP=true "
        "to restrict each tournament to its owner."
    )

MANAGER = "manager"
SCORER = "scorer"

# Actions a scorer may perform; anything else needs manager or ownership.
SCORER_ACTIONS = {
    "match.start", "match.pause", "match.resume",
    "match.score", "match.confirm", "match.walkover", "match.add_board",
}

_ownership_available: Optional[bool] = None


def ownership_enforced() -> Optional[bool]:
    """True/False once probed, None before the first check."""
    return _ownership_available


def _mark(available: bool) -> None:
    global _ownership_available
    if _ownership_available != available and not available:
        logger.warning(
            "Tournament ownership columns are missing — any admin can manage any "
            "tournament. Apply backend/db/migrations/003_ownership_and_access.sql."
        )
    _ownership_available = available


def _looks_missing(error: Exception) -> bool:
    text = str(error).lower()
    return any(m in text for m in (
        "owner_id", "tournament_access", "does not exist", "42703", "42p01", "pgrst205",
    ))


def load_tournament(admin_db, tournament_id: str) -> Dict[str, Any]:
    rows = admin_db.table("tournaments").select("*").eq("id", tournament_id).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Tournament not found.")
    return rows[0]


def get_access_row(admin_db, tournament_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    try:
        rows = admin_db.table("tournament_access").select("*").eq(
            "tournament_id", tournament_id).eq("user_id", user_id).execute().data
        _mark(True)
        return rows[0] if rows else None
    except Exception as e:
        if _looks_missing(e):
            _mark(False)
            return None
        raise


def describe_access(admin_db, tournament: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    What this user may do with this tournament.

    Returned to the frontend so it can show the right controls rather than
    offering buttons that will be refused.
    """
    user_id = profile.get("id")
    owner_id = tournament.get("owner_id")
    is_admin = profile.get("role") == "admin"

    def unenforced() -> Dict[str, Any]:
        # Un-migrated database: preserve the previous behaviour, but report
        # honestly that nothing is being enforced.
        return {"isOwner": False, "role": MANAGER if is_admin else None,
                "canManage": is_admin, "canScore": is_admin,
                "enforced": False, "status": None}

    # Policy, as opposed to the migration probe below: on a single-operator
    # deployment every admin is the same person, and being told a tournament
    # "is owned by Sets Admin; request access to help run it" is an obstacle
    # with nobody on the other end to grant it. Note this widens access for
    # admins only -- a player still falls through to the ordinary checks.
    if is_admin and not settings.ENFORCE_TOURNAMENT_OWNERSHIP:
        is_owner = bool(owner_id and user_id and str(owner_id) == str(user_id))
        return {"isOwner": is_owner, "role": MANAGER,
                "canManage": True, "canScore": True,
                "enforced": False, "status": "owner" if is_owner else "unenforced"}

    if owner_id is None and ownership_enforced() is False:
        return unenforced()

    if owner_id and user_id and str(owner_id) == str(user_id):
        return {"isOwner": True, "role": MANAGER, "canManage": True, "canScore": True,
                "enforced": True, "status": "owner"}

    row = get_access_row(admin_db, tournament["id"], user_id) if user_id else None

    # get_access_row is what probes for the access table, so the un-migrated
    # case can only be detected here -- checking before it would always see
    # "unknown" on the first call.
    if owner_id is None and ownership_enforced() is False:
        return unenforced()

    if row and row.get("status") == "approved":
        role = row.get("access_role") or MANAGER
        return {"isOwner": False, "role": role,
                "canManage": role == MANAGER, "canScore": True,
                "enforced": True, "status": "approved"}

    # An unowned tournament (created before migration 003 and never adopted)
    # stays manageable by any admin, otherwise it would be stranded.
    if owner_id is None and profile.get("role") == "admin":
        return {"isOwner": False, "role": MANAGER, "canManage": True, "canScore": True,
                "enforced": True, "status": "unowned"}

    return {"isOwner": False, "role": None, "canManage": False, "canScore": False,
            "enforced": True, "status": (row or {}).get("status")}


def require_tournament_access(
    admin_db,
    tournament_id: str,
    profile: Dict[str, Any],
    action: str = "tournament.manage",
) -> Dict[str, Any]:
    """
    Authorise `profile` to perform `action` on this tournament, or raise 403.

    Returns the tournament row so callers do not have to load it twice.
    """
    tournament = load_tournament(admin_db, tournament_id)

    if profile.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden. Action requires admin rights.")

    access = describe_access(admin_db, tournament, profile)

    if access["canManage"]:
        return tournament
    if action in SCORER_ACTIONS and access["canScore"]:
        return tournament

    owner_name = None
    owner_id = tournament.get("owner_id")
    if owner_id:
        rows = admin_db.table("profiles").select("name").eq("id", owner_id).execute().data
        owner_name = rows[0]["name"] if rows else None

    status = access.get("status")
    if status == "pending":
        detail = "Your access request for this tournament is still awaiting the owner's decision."
    elif status == "rejected":
        detail = "Your access request for this tournament was declined."
    elif status == "revoked":
        detail = "Your access to this tournament has been revoked."
    elif action in SCORER_ACTIONS:
        detail = "You do not have scoring access to this tournament."
    else:
        detail = "Only the tournament owner can do this."

    if owner_name:
        detail += f" It is owned by {owner_name}; request access to help run it."

    raise HTTPException(status_code=403, detail=detail)


def owned_by(admin_db, tournament_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Owner-only actions: deciding access requests, transferring ownership."""
    tournament = load_tournament(admin_db, tournament_id)
    owner_id = tournament.get("owner_id")

    if owner_id is None:
        if profile.get("role") == "admin":
            return tournament
        raise HTTPException(status_code=403, detail="Forbidden. Action requires admin rights.")

    if str(owner_id) != str(profile.get("id")):
        raise HTTPException(
            status_code=403,
            detail="Only the tournament owner can manage access to it.",
        )
    return tournament


def set_owner_on_create(payload: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """Stamp the creator as owner, tolerating a database without the column."""
    if ownership_enforced() is False:
        return payload
    payload = dict(payload)
    payload["owner_id"] = profile.get("id")
    return payload


def strip_owner(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Retry helper for a database that predates migration 003."""
    payload = dict(payload)
    payload.pop("owner_id", None)
    return payload


def approved_managers(admin_db, tournament_id: str) -> List[str]:
    """Owner plus everyone with approved access — the audience for access events."""
    ids: List[str] = []
    try:
        rows = admin_db.table("tournaments").select("owner_id").eq(
            "id", tournament_id).execute().data
        if rows and rows[0].get("owner_id"):
            ids.append(rows[0]["owner_id"])
    except Exception:
        pass
    try:
        rows = admin_db.table("tournament_access").select("user_id").eq(
            "tournament_id", tournament_id).eq("status", "approved").execute().data or []
        ids.extend(r["user_id"] for r in rows)
    except Exception as e:
        if not _looks_missing(e):
            raise
    return list(dict.fromkeys(ids))
