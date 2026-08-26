"""
Administrative audit trail (spec 83).

`audit_logs` existed in the schema but nothing ever wrote to it. Every sensitive
administrative operation now records who did what, to which entity, and what the
state was before and after.

Writing an audit record must never break the operation it describes, so failures
are logged rather than raised.
"""
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger("uvicorn.error")

# Field names that must never be persisted into the audit trail (spec 88:
# "log without exposing sensitive information").
REDACTED_FIELDS = {"password", "access_token", "refresh_token", "service_role_key", "api_key"}


def _scrub(state: Optional[Any]) -> Optional[Any]:
    if isinstance(state, dict):
        return {
            k: ("***" if k.lower() in REDACTED_FIELDS else _scrub(v))
            for k, v in state.items()
        }
    if isinstance(state, list):
        return [_scrub(v) for v in state]
    return state


def record_audit(
    admin_db,
    *,
    actor: Optional[Dict[str, Any]],
    action: str,
    entity_type: str,
    entity_id: str,
    previous_state: Optional[Dict[str, Any]] = None,
    new_state: Optional[Dict[str, Any]] = None,
    request_context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Append one audit record.

    `action` is a dotted verb such as "tournament.create" or "registration.approve".
    """
    try:
        context = dict(request_context or {})
        if actor:
            context.setdefault("actor_name", actor.get("name"))
            context.setdefault("actor_role", actor.get("role"))

        admin_db.table("audit_logs").insert({
            "user_id": (actor or {}).get("id"),
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "previous_state": _scrub(previous_state),
            "new_state": _scrub(new_state),
            "request_context": context or None,
        }).execute()
    except Exception as e:
        logger.error(f"Audit write failed for {action} on {entity_type}:{entity_id}: {str(e)}")
