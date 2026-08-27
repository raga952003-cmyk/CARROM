from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.database import get_db, get_admin_db, get_user_client
import logging

logger = logging.getLogger("uvicorn.error")

# auto_error=False so a missing/malformed header returns 401 (not HTTPBearer's
# default 403). The frontend only clears a stale token on 401.
security_bearer = HTTPBearer(auto_error=False)


def get_access_token(credentials: HTTPAuthorizationCredentials = Depends(security_bearer)) -> str:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Missing bearer access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def get_current_user(token: str = Depends(get_access_token)):
    """
    Verify the caller's access token.

    Verification runs on a per-request client. The module-level client is
    shared by every concurrent request and `auth.get_user()` mutates its
    session state, so verifying on it made overlapping requests interfere with
    each other -- which surfaced as intermittent 500s while the UI polled.
    """
    try:
        client = get_user_client(token)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        res = client.auth.get_user(token)
    except Exception as e:
        logger.info(f"Token verification rejected: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Session expired or invalid. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not res or not res.user:
        raise HTTPException(
            status_code=401,
            detail="Session expired or invalid. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return res.user


def get_user_db(token: str = Depends(get_access_token)):
    """
    Supabase client bound to the caller's JWT, so RLS policies that depend on
    auth.uid() evaluate against the real user instead of the anon role.
    """
    try:
        return get_user_client(token)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


def _metadata(user, name: str) -> dict:
    """user_metadata / app_metadata, tolerating None or a missing attribute."""
    value = getattr(user, name, None)
    return value if isinstance(value, dict) else {}


def _profile_from_token(user) -> dict:
    """
    Minimal profile assembled from the token's claims.

    Used when the profiles row has not been created yet (or was removed): the
    caller is authenticated, so the request should still succeed rather than
    fail with a server error.
    """
    app_meta = _metadata(user, "app_metadata")
    user_meta = _metadata(user, "user_metadata")
    return {
        "id": getattr(user, "id", None),
        "email": getattr(user, "email", None),
        "name": user_meta.get("name") or "User",
        "role": app_meta.get("role") or user_meta.get("role") or "player",
        "rating": user_meta.get("rating") or 1500,
        "club": user_meta.get("club") or "Independent",
        "city": user_meta.get("city") or "Pune",
    }


def get_user_profile(user = Depends(get_current_user)):
    """
    The caller's profile row, falling back to their token claims.

    A missing profile row is not a server error, and a transient read failure
    should not take down every authenticated endpoint, so both degrade to the
    token-derived profile instead of returning 500.
    """
    user_id = getattr(user, "id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Token carries no user id.")

    try:
        # Service client: this is an internal identity lookup, and it must not
        # depend on a SELECT policy being present for the caller's role.
        res = get_admin_db().table("profiles").select("*").eq("id", user_id).execute()
        if res.data:
            return res.data[0]
        logger.warning(f"No profile row for authenticated user {user_id}; using token claims.")
    except Exception as e:
        logger.error(f"Profile lookup failed for {user_id}, using token claims: {str(e)}")

    return _profile_from_token(user)


def verify_admin(profile = Depends(get_user_profile)):
    if profile.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden. Action requires admin rights.")
    return profile


def get_optional_profile(
    credentials: HTTPAuthorizationCredentials = Depends(security_bearer),
):
    """
    The caller's profile when they present a valid token, otherwise None.

    For endpoints that are readable without signing in but should return more
    to an authenticated admin. An invalid token is treated as anonymous rather
    than an error, so a stale token cannot break a public page.
    """
    if not credentials or not credentials.credentials:
        return None
    try:
        user = get_current_user(credentials.credentials)
        return get_user_profile(user)
    except HTTPException:
        return None
    except Exception:
        return None
