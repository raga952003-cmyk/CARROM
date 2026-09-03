from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db, get_admin_db
from app.models.notification import NotificationCreateSchema
from app.utils.security import get_user_profile, verify_admin, get_user_db
from app.utils.serializers import serialize_notification
from app.services.notification_service import fan_out_notification
from app.services.access_control import require_tournament_access
from typing import List, Dict, Any

router = APIRouter(prefix="/notifications", tags=["notifications"])

# The drawer shows a scrollable list and the header badge stops counting at
# "9+", so nothing on screen can use more than this. Unbounded, it grew with
# every announcement ever fanned out -- one row per recipient per event -- and
# the organiser who had run the most tournaments waited the longest, on a read
# that fires again after every single admin action.
NOTIFICATION_PAGE = 200


@router.get("")
async def get_notifications(profile = Depends(get_user_profile), db = Depends(get_user_db)):
    # The JWT-bound client is required here: the RLS policy filters on
    # auth.uid(), which is NULL on the shared anon client.
    try:
        res = db.table("notifications").select("*, tournament:tournaments(name)").or_(
            f"profile_id.is.null,profile_id.eq.{profile['id']}"
        # Unread first, then newest. The cap is what the drawer can show, but
        # the header's badge is counted from this same list, so an account
        # holding more than a page of history would have stopped counting
        # unread ones older than the newest 200 and quietly shown nothing to
        # read. Ordering by `read` puts every unread row inside the page.
        ).order("read").order("created_at", desc=True).limit(NOTIFICATION_PAGE).execute()
        return [serialize_notification(n) for n in (res.data or [])]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/read-all")
async def mark_all_read(profile = Depends(get_user_profile), db = Depends(get_user_db)):
    try:
        res = db.table("notifications").update({"read": True}).eq(
            "profile_id", profile["id"]
        ).eq("read", False).execute()
        return {
            "status": "success",
            "updated": len(res.data or []),
            "message": "All notifications marked as read."
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{id}/read")
async def mark_read(id: str, profile = Depends(get_user_profile), db = Depends(get_user_db)):
    try:
        res = db.table("notifications").update({"read": True}).eq("id", id).execute()
        if not res.data:
            # Either the id does not exist or it is a shared broadcast row that
            # this user is not allowed to mutate.
            raise HTTPException(
                status_code=404,
                detail="Notification not found or not addressed to this user."
            )
        return serialize_notification(res.data[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("")
async def create_notification(data: NotificationCreateSchema, admin = Depends(verify_admin)):
    admin_db = get_admin_db()
    try:
        # Announcing something to a tournament's audience is the organiser's
        # voice; anyone else broadcasting under it would be impersonation.
        if data.tournament_id:
            require_tournament_access(admin_db, data.tournament_id, admin)

        if data.profile_id:
            payload = {
                "title": data.title,
                "message": data.message,
                "type": data.type,
                "profile_id": data.profile_id,
                "tournament_id": data.tournament_id,
                "read": False
            }
            res = admin_db.table("notifications").insert(payload).execute()
            return serialize_notification(res.data[0])

        # No explicit recipient: deliver a copy to everyone in the audience so
        # each of them can mark their own as read.
        delivered = fan_out_notification(
            admin_db,
            title=data.title,
            message=data.message,
            type=data.type,
            tournament_id=data.tournament_id,
        )
        return {"status": "success", "delivered": delivered}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
