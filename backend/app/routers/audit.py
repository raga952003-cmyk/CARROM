"""
Audit domain (spec 61, 83).

Read-only and admin-only. Audit records are append-only at the database level
(see migration 002), so there is deliberately no update or delete route here.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_admin_db
from app.utils.security import verify_admin
from app.utils.serializers import camelize
from typing import Optional

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
async def list_audit_logs(
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin = Depends(verify_admin),
):
    """Administrative audit trail, newest first. Paginated (spec 91)."""
    admin_db = get_admin_db()
    try:
        query = admin_db.table("audit_logs").select("*", count="exact")
        if entity_type:
            query = query.eq("entity_type", entity_type)
        if entity_id:
            query = query.eq("entity_id", entity_id)
        if user_id:
            query = query.eq("user_id", user_id)
        if action:
            query = query.eq("action", action)

        res = query.order("timestamp", desc=True).range(offset, offset + limit - 1).execute()
        return {
            "total": res.count,
            "limit": limit,
            "offset": offset,
            "entries": [camelize(row) for row in (res.data or [])],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scores/{match_id}")
async def list_score_audit(match_id: str, admin = Depends(verify_admin)):
    """Board-by-board score correction history for one match."""
    admin_db = get_admin_db()
    try:
        res = admin_db.table("score_audit_logs").select("*").eq(
            "match_id", match_id
        ).order("timestamp", desc=True).execute()
        return [camelize(row) for row in (res.data or [])]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
