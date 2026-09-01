from fastapi import APIRouter, Depends, HTTPException, status
from app.database import get_db, get_admin_db
from app.models.auth import SignUpSchema, LoginSchema, RefreshSchema
from app.utils.security import get_user_profile, get_current_user
from app.utils.serializers import serialize_player
from typing import Dict, Any
import logging

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/auth", tags=["auth"])

def _session_response(session, profile_data) -> Dict[str, Any]:
    """
    Auth response including the refresh token.

    Supabase access tokens expire after about an hour. Returning only the
    access token left the client with no way to renew, so every session died
    after an hour and the app then hammered the API with 401s.
    """
    return {
        "access_token": session.access_token,
        "refresh_token": getattr(session, "refresh_token", None),
        "expires_at": getattr(session, "expires_at", None),
        "expires_in": getattr(session, "expires_in", None),
        "token_type": "bearer",
        "user": serialize_player(profile_data),
    }



@router.post("/signup")
async def signup(data: SignUpSchema):
    supabase = get_db()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database client not configured.")
    
    admin_supabase = get_admin_db()
    try:
        logger.info(f"Step 1: Attempting user signup via Admin API for {data.email}")
        user_response = admin_supabase.auth.admin.create_user({
            "email": data.email,
            "password": data.password,
            "email_confirm": True,
            "user_metadata": {
                "name": data.name,
                # Hard-coded, never taken from the request. app_metadata is what
                # the API trusts for authorisation, so accepting a role here let
                # anyone who could reach the sign-up form mint themselves an
                # admin account.
                "role": "player",
                "club": data.club,
                "city": data.city,
                "phone": data.phone,
                "rating": data.rating
            },
            "app_metadata": {
                "role": "player"
            }
        })
        
        # create_user returns a UserResponse wrapper; the id lives on .user
        created_user = getattr(user_response, "user", None) if user_response else None
        if not created_user:
            logger.error("Admin signup returned no user.")
            raise HTTPException(status_code=400, detail="Signup failed. Please try again.")

        user_id = created_user.id
        logger.info(f"Step 2: Admin user creation successful. Created auth user: {user_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Signup exception details: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    try:
        # The handle_new_user trigger only copies name/email/role/rating, so the
        # remaining profile fields are written here.
        profile_extras = {
            k: v for k, v in {
                "club": data.club,
                "city": data.city,
                "phone": data.phone,
            }.items() if v is not None
        }
        if profile_extras:
            admin_supabase.table("profiles").update(profile_extras).eq("id", user_id).execute()

        # Log the user in programmatically to generate an access token session
        logger.info(f"Step 3: Programmatic sign in to generate session token for {data.email}")
        login_response = supabase.auth.sign_in_with_password({
            "email": data.email,
            "password": data.password
        })
        if not login_response or not login_response.session:
            raise HTTPException(status_code=400, detail="Account created but session could not be started.")

        # Retrieve the profile that was created by the Postgres trigger
        logger.info("Step 4: Fetching user profile from profiles table")
        profile_response = admin_supabase.table("profiles").select("*").eq("id", user_id).execute()
        profile_data = profile_response.data[0] if profile_response.data else {
            "id": user_id,
            "email": data.email,
            "name": data.name,
            "role": "player",
            "club": data.club,
            "city": data.city,
            "phone": data.phone,
            "rating": data.rating,
            "created_at": ""
        }

        logger.info("Step 5: Signup workflow complete.")
        return _session_response(login_response.session, profile_data)
    except Exception as e:
        # Roll the auth user back so a half-finished signup does not leave an
        # orphaned account that blocks the user from retrying with that email.
        logger.error(f"Signup failed after user creation, rolling back {user_id}: {str(e)}")
        try:
            admin_supabase.auth.admin.delete_user(user_id)
        except Exception as cleanup_error:
            logger.error(f"Could not roll back auth user {user_id}: {str(cleanup_error)}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login")
async def login(data: LoginSchema):
    supabase = get_db()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database client not configured.")
    
    try:
        # Sign in via Supabase Auth
        auth_response = supabase.auth.sign_in_with_password({
            "email": data.email,
            "password": data.password
        })
        
        if not auth_response or not auth_response.session:
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        
        # Load the user profile
        profile_response = supabase.table("profiles").select("*").eq("id", auth_response.user.id).execute()
        
        if not profile_response.data:
            # If database record is not present, create it using metadata
            # Wait, the trigger should have run, but in case of manual sync issues
            # Falls back to player, not to whatever the caller asked to sign in as:
            # a missing profile row must not become a promotion.
            role = (auth_response.user.app_metadata.get("role")
                    or auth_response.user.user_metadata.get("role") or "player")
            name = auth_response.user.user_metadata.get("name") or "User"
            
            profile_data = {
                "id": auth_response.user.id,
                "email": auth_response.user.email,
                "name": name,
                "role": role,
                "rating": 1500,
                "club": "Independent",
                "city": None
            }
        else:
            profile_data = profile_response.data[0]

        # Verify the requested role matches their database profile
        if profile_data.get("role") != data.role:
            raise HTTPException(
                status_code=403, 
                detail=f"Forbidden. You do not have permissions for the role: {data.role}."
            )

        return _session_response(auth_response.session, profile_data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/me")
async def get_me(profile: Dict[str, Any] = Depends(get_current_user)):
    # Simply load from security check
    supabase = get_db()
    res = supabase.table("profiles").select("*").eq("id", profile.id).execute()
    if res.data:
        return serialize_player(res.data[0])
    return {
        "id": profile.id,
        "email": profile.email,
        "name": profile.user_metadata.get("name") or "User",
        "role": profile.app_metadata.get("role") or "player"
    }


@router.post("/refresh")
async def refresh_session(data: RefreshSchema):
    """
    Exchange a refresh token for a fresh access token.

    Lets a long-lived session survive access-token expiry without forcing the
    user back to the sign-in screen.
    """
    supabase = get_db()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database client not configured.")

    try:
        result = supabase.auth.refresh_session(data.refresh_token)
    except Exception as e:
        logger.info(f"Refresh rejected: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Your session has expired. Please sign in again.",
        )

    session = getattr(result, "session", None)
    user = getattr(result, "user", None)
    if not session or not session.access_token:
        raise HTTPException(
            status_code=401,
            detail="Your session has expired. Please sign in again.",
        )

    profile_data = None
    if user is not None:
        rows = get_admin_db().table("profiles").select("*").eq("id", user.id).execute().data
        profile_data = rows[0] if rows else None
    if profile_data is None:
        profile_data = {"id": getattr(user, "id", None), "email": getattr(user, "email", None)}

    return _session_response(session, profile_data)
