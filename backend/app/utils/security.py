from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.database import get_db, get_user_client
import logging

logger = logging.getLogger("uvicorn.error")

# auto_error=False so a missing/!malformed header returns 401 (not HTTPBearer's
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
    supabase = get_db()
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase connection not initialized.")

    try:
        # Validate session against Supabase Auth API
        res = supabase.auth.get_user(token)
    except Exception as e:
        logger.error(f"JWT verification error: {str(e)}")
        raise HTTPException(status_code=401, detail="Authentication failed. Invalid JWT access token.")

    if not res or not res.user:
        raise HTTPException(status_code=401, detail="Authentication session invalid or expired.")
    return res.user

def get_user_db(token: str = Depends(get_access_token)):
    """
    Supabase client bound to the caller's JWT, so RLS policies that depend on
    auth.uid() (notifications, own-profile updates) evaluate against the real
    user instead of the anon role.
    """
    try:
        return get_user_client(token)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_user_profile(user = Depends(get_current_user)):
    supabase = get_db()
    try:
        res = supabase.table("profiles").select("*").eq("id", user.id).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]

        # Fallback to metadata if profile row not synced yet
        role = user.app_metadata.get("role") or user.user_metadata.get("role") or "player"
        name = user.user_metadata.get("name") or "User"
        return {
            "id": user.id,
            "email": user.email,
            "name": name,
            "role": role,
            "rating": 1500,
            "club": "Independent",
            "city": "Pune"
        }
    except Exception as e:
        logger.error(f"Profile fetch error: {str(e)}")
        raise HTTPException(status_code=500, detail="Database error while loading profile.")

def verify_admin(profile = Depends(get_user_profile)):
    if profile.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden. Action requires admin rights.")
    return profile
