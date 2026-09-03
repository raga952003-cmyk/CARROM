from fastapi import APIRouter, Depends, HTTPException, Request, status
from app.database import get_db, get_admin_db
from app.config import settings
from app.models.auth import (
    SignUpSchema, LoginSchema, RefreshSchema,
    ProfileUpdateSchema, PasswordChangeSchema, EmailChangeSchema,
    ForgotPasswordSchema, ResetPasswordSchema,
)
from app.utils.security import get_user_profile, get_current_user
from app.utils.serializers import serialize_player
from typing import Dict, Any
import logging

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/auth", tags=["auth"])


SIGNUP_ROLES = ("player", "admin")


def _role_for_signup(requested) -> str:
    """
    The role a registration asked for, checked for spelling and nothing else.

    Registration is open in this deployment: whoever fills in the form picks
    the role and gets it. That is a deliberate choice and worth being plain
    about -- there is no key, no invitation and no approval step, so anybody
    who can load the sign-up page can make themselves an administrator of
    every tournament this instance runs.

    What is still refused is a role that is not one of the two. An unknown
    value used to fall through to 'player', which turns a typo into an account
    that quietly is not what its owner believes it is.
    """
    role = (requested or "player").strip().lower()
    if role not in SIGNUP_ROLES:
        raise HTTPException(
            status_code=422,
            detail="Role must be either 'player' or 'admin'.",
        )
    return role

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
        "user": serialize_player(profile_data, include_contact=True),
    }



@router.post("/signup")
async def signup(data: SignUpSchema):
    supabase = get_db()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database client not configured.")
    
    admin_supabase = get_admin_db()

    # Settled before anything is written, so a bad value costs no account.
    role = _role_for_signup(data.role)

    try:
        logger.info(f"Step 1: Attempting user signup via Admin API for {data.email}")
        user_response = admin_supabase.auth.admin.create_user({
            "email": data.email,
            "password": data.password,
            "email_confirm": True,
            "user_metadata": {
                "name": data.name,
                # app_metadata is what the API trusts for authorisation, so
                # this is the line that makes the choice on the sign-up form
                # real. It is written from _role_for_signup and nowhere else,
                # which is what keeps the two copies of the role in step.
                "role": role,
                "club": data.club,
                "city": data.city,
                "phone": data.phone,
                "rating": data.rating
            },
            "app_metadata": {
                "role": role
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
        # The role is written here as well as in the metadata above. The
        # trigger reads it out of app_metadata, but a database without the
        # trigger has its profile row healed from token claims instead, and an
        # admin whose profiles row says 'player' is refused at every door --
        # including the sign-in that would let anyone put it right.
        profile_extras["role"] = role
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
            "role": role,
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
            # No profiles row yet -- the trigger should have made one, but a
            # database without it has to sign in too.
            #
            # app_metadata only. Falling back to user_metadata was reading the
            # one field the account holder can write for themselves with
            # nothing but the anon key the browser already ships: set
            # user_metadata.role to "admin", delete or lose the profiles row,
            # and sign in as an administrator. It falls back to player, not to
            # whatever the caller asked to sign in as, for the same reason.
            role = auth_response.user.app_metadata.get("role") or "player"
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

        # Verify the requested role matches their database profile.
        #
        # The password has already been accepted at this point, so naming the
        # role the account actually holds tells the caller nothing they could
        # not find out by trying the other tab -- and it is the difference
        # between a door that says no and one that says which door to use.
        actual = profile_data.get("role")
        if actual != data.role:
            where = "Player Portal" if actual == "player" else "Admin Console"
            raise HTTPException(
                status_code=403,
                detail=(
                    f"This account is registered as {'an' if actual == 'admin' else 'a'} "
                    f"{actual}, not {'an' if data.role == 'admin' else 'a'} {data.role}. "
                    f"Sign in through the {where}"
                    + (", or ask an organiser to promote the account."
                       if actual == "player" else ".")
                ),
            )

        return _session_response(auth_response.session, profile_data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/me")
async def get_me(profile: Dict[str, Any] = Depends(get_user_profile)):
    """
    Who the caller is, read from the profiles row that every other route also
    reads.

    It used to look the row up on the ANON client, which RLS does not let see
    other people's profiles -- so the read came back empty and the answer was
    assembled from the token's own claims instead. Two things followed. The
    role became whatever app_metadata said when the token was minted, so a
    promotion or demotion was invisible until the token expired, and the app
    routed a reload on a different source than it routed a sign-in on. And the
    reply carried four fields instead of the whole profile, so opening Settings
    after a reload showed an empty club, city, phone and avatar -- and saving
    that form wrote the blanks back.

    get_user_profile is the same dependency the rest of the API authorises on:
    service client, heals a missing row rather than inventing an identity, and
    reports a failed read as a failure instead of guessing.
    """
    return serialize_player(profile, include_contact=True)


# Roughly 300 KB of base64, which is a generous 256x256 JPEG. The browser
# resizes before sending; this is the backstop for anything that does not.
MAX_AVATAR_CHARS = 300_000


@router.put("/me")
async def update_me(data: ProfileUpdateSchema,
                    profile: Dict[str, Any] = Depends(get_current_user)):
    """
    Change your own profile.

    Deliberately narrow: name, club, city, phone and avatar. Role is not here
    and never will be -- that is what let anyone make themselves an admin
    through the sign-up form. Email and password have their own endpoints
    below, because both need the current password.
    """
    admin_db = get_admin_db()

    patch: Dict[str, Any] = {}
    for field in ("name", "club", "city", "phone"):
        value = getattr(data, field)
        if value is not None:
            patch[field] = value.strip() or None

    if data.avatar is not None:
        avatar = data.avatar.strip()
        if avatar == "":
            patch["avatar"] = None            # Explicitly removing the picture.
        else:
            if not avatar.startswith("data:image/"):
                raise HTTPException(
                    status_code=422,
                    detail="The picture must be an image.",
                )
            if len(avatar) > MAX_AVATAR_CHARS:
                raise HTTPException(
                    status_code=413,
                    detail="That picture is too large. Choose a smaller one.",
                )
            patch["avatar"] = avatar

    if patch.get("name") == "":
        raise HTTPException(status_code=422, detail="A name is required.")
    if not patch:
        raise HTTPException(status_code=422, detail="Nothing to change.")

    try:
        res = admin_db.table("profiles").update(patch).eq("id", profile.id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Profile not found.")
        # The name is mirrored into auth metadata, which other places read.
        if "name" in patch:
            admin_db.auth.admin.update_user_by_id(
                profile.id, attributes={"user_metadata": {"name": patch["name"]}}
            )
        return serialize_player(res.data[0], include_contact=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Profile update failed for {profile.id}: {str(e)}")
        raise HTTPException(status_code=400, detail="Could not save those changes.")


def _verify_password(email: str, password: str) -> None:
    """Confirm the caller knows the current password, or raise 403."""
    try:
        get_db().auth.sign_in_with_password({"email": email, "password": password})
    except Exception:
        raise HTTPException(
            status_code=403,
            detail="That is not your current password.",
        )


@router.post("/password")
async def change_password(data: PasswordChangeSchema,
                          profile: Dict[str, Any] = Depends(get_current_user)):
    """
    Change your own password.

    The current one is required. A stolen or forgotten-unlocked session should
    not be enough to lock the real owner out of their account.
    """
    if data.new_password == data.current_password:
        raise HTTPException(
            status_code=422,
            detail="The new password is the same as the current one.",
        )
    _verify_password(profile.email, data.current_password)
    try:
        get_admin_db().auth.admin.update_user_by_id(
            profile.id, attributes={"password": data.new_password}
        )
    except Exception as e:
        logger.error(f"Password change failed for {profile.id}: {str(e)}")
        raise HTTPException(status_code=400, detail="Could not change the password.")
    return {"status": "success",
            "message": "Password changed. Your other sessions stay signed in."}


@router.post("/email")
async def change_email(data: EmailChangeSchema,
                       profile: Dict[str, Any] = Depends(get_current_user)):
    """Change the address you sign in with. Requires the current password."""
    new_email = str(data.new_email).strip().lower()
    if new_email == (profile.email or "").lower():
        raise HTTPException(status_code=422, detail="That is already your email address.")
    _verify_password(profile.email, data.current_password)

    admin_db = get_admin_db()
    taken = admin_db.table("profiles").select("id").eq("email", new_email).execute().data or []
    if taken:
        raise HTTPException(status_code=409, detail="Another account already uses that address.")

    try:
        admin_db.auth.admin.update_user_by_id(
            profile.id, attributes={"email": new_email, "email_confirm": True}
        )
        admin_db.table("profiles").update({"email": new_email}).eq("id", profile.id).execute()
    except Exception as e:
        logger.error(f"Email change failed for {profile.id}: {str(e)}")
        raise HTTPException(status_code=400, detail="Could not change the email address.")
    return {"status": "success",
            "message": "Email changed. Use the new address next time you sign in."}


@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordSchema, request: Request):
    """
    Send a reset link.

    The comment on admin-created accounts has always said they "use the
    Supabase password-reset flow". That flow was never built, so an account
    whose password nobody knows -- which is every account created from a sheet
    -- had no way back in at all.

    The response is the same whether or not the address exists. Saying "no such
    account" turns this endpoint into a way to find out who has one.
    """
    email = str(data.email).strip().lower()
    same_answer = {
        "status": "success",
        "message": (
            "If that address has an account, a reset link is on its way. "
            "Check the spam folder if it does not arrive."
        ),
    }

    # Send them back to the site they asked from.
    #
    # This used to read CORS_ORIGINS, which is not set on this deployment, so
    # the redirect was omitted and Supabase fell back to its project-level Site
    # URL -- localhost:3000, which is nobody's browser. Taking it from the
    # request works wherever the app is served: production, a preview build, or
    # a laptop.
    #
    # The value is not a redirect this API performs, and it is not trusted on
    # its own: Supabase refuses any target that is not in the project's
    # allow-list, so a forged Origin cannot point the link somewhere else.
    origin = (request.headers.get("origin") or "").rstrip("/")
    if not origin:
        referer = request.headers.get("referer") or ""
        if "//" in referer:
            scheme, _, rest = referer.partition("//")
            origin = "{}//{}".format(scheme, rest.split("/", 1)[0])
    if not origin:
        origin = (settings.cors_origin_list() or [""])[0].rstrip("/")

    try:
        # The site root, with no fragment of its own.
        #
        # Supabase APPENDS its token as a fragment -- "#access_token=...&
        # type=recovery" -- so a redirect that already ended in "#/reset-password"
        # would arrive carrying two. The app recognises the recovery fragment and
        # renders the set-password page from it, so the root is enough.
        options = {"redirect_to": "{}/".format(origin)} if origin else {}
        get_db().auth.reset_password_for_email(email, options)
    except Exception as e:
        # Logged, not returned: a delivery failure is ours to fix, and telling
        # the caller which addresses error would leak the same thing the
        # identical response above is there to hide.
        logger.error(f"Password reset for {email} failed: {str(e)}")

    return same_answer


@router.post("/reset-password")
async def reset_password(data: ResetPasswordSchema):
    """
    Set a new password using the token from a reset link.

    Supabase's own flow expects the browser to hold a Supabase client and call
    updateUser itself. This deployment has no such client -- the VITE_SUPABASE_*
    variables are not set in the build -- so the token comes here instead and
    the change is made with the service role after the token is verified.
    """
    token = (data.access_token or "").strip()
    if not token:
        raise HTTPException(status_code=422, detail="That reset link is missing its token.")

    try:
        # The token identifies the account; an expired or forged one resolves
        # to nobody, and this is the only thing standing between a link and a
        # password change.
        result = get_admin_db().auth.get_user(token)
        user = getattr(result, "user", None)
    except Exception:
        user = None

    if not user or not getattr(user, "id", None):
        raise HTTPException(
            status_code=401,
            detail="That reset link has expired or has already been used. Ask for a new one.",
        )

    try:
        get_admin_db().auth.admin.update_user_by_id(
            user.id, attributes={"password": data.new_password}
        )
    except Exception as e:
        logger.error(f"Password reset write failed for {user.id}: {str(e)}")
        raise HTTPException(status_code=400, detail="Could not set that password.")

    return {
        "status": "success",
        "message": "Password set. Sign in with your new password.",
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
