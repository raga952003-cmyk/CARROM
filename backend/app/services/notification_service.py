"""
Notification delivery.

`notifications.profile_id IS NULL` means "public announcement", but `read` is a
column on that single shared row -- so one user marking a broadcast as read
would flip it for everybody, and RLS gives a plain user no way to update a row
that isn't theirs. Announcements are therefore fanned out into one row per
recipient, which makes both per-user read state and the RLS policies work.
"""
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger("uvicorn.error")


def resolve_tournament_audience(admin_db, tournament_id: Optional[str]) -> List[str]:
    """
    Profile ids that should receive a tournament announcement: every approved
    participant (both halves of a doubles team) plus every admin.
    """
    recipient_ids = set()

    if tournament_id:
        regs = admin_db.table("registrations").select(
            "player_id, team:teams(player1_id, player2_id)"
        ).eq("tournament_id", tournament_id).eq("status", "approved").execute().data or []

        for reg in regs:
            if reg.get("player_id"):
                recipient_ids.add(reg["player_id"])
            team = reg.get("team")
            if team:
                for key in ("player1_id", "player2_id"):
                    if team.get(key):
                        recipient_ids.add(team[key])

    admins = admin_db.table("profiles").select("id").eq("role", "admin").execute().data or []
    recipient_ids.update(a["id"] for a in admins)

    return list(recipient_ids)


def fan_out_notification(
    admin_db,
    title: str,
    message: str,
    type: str,
    tournament_id: Optional[str] = None,
    recipient_ids: Optional[List[str]] = None,
) -> int:
    """
    Insert one notification row per recipient. Returns the number delivered.

    Never raises: a failed announcement must not roll back the match result or
    schedule publish that triggered it.
    """
    try:
        if recipient_ids is None:
            recipient_ids = resolve_tournament_audience(admin_db, tournament_id)

        if not recipient_ids:
            return 0

        rows: List[Dict[str, Any]] = [
            {
                "profile_id": profile_id,
                "tournament_id": tournament_id,
                "title": title,
                "message": message,
                "type": type,
                "read": False,
            }
            for profile_id in recipient_ids
        ]
        admin_db.table("notifications").insert(rows).execute()
        return len(rows)
    except Exception as e:
        logger.error(f"Failed to deliver notification '{title}': {str(e)}")
        return 0
