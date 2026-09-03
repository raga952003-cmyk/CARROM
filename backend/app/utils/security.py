from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.database import get_db, get_admin_db, get_user_client
from app.config import settings
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


class _TokenUser:
    """
    The subset of the Supabase user object the application reads, built from
    verified JWT claims. Same attribute surface as the client's own model, so
    callers cannot tell which verification path produced it.
    """

    __slots__ = ("id", "email", "user_metadata", "app_metadata", "aud", "role")

    def __init__(self, claims: dict):
        self.id = claims.get("sub")
        self.email = claims.get("email")
        self.user_metadata = claims.get("user_metadata") or {}
        self.app_metadata = claims.get("app_metadata") or {}
        self.aud = claims.get("aud")
        self.role = claims.get("role")


def _verify_locally(token: str):
    """
    Verify the access token's signature with the project's JWT secret.

    Supabase signs these itself, so asking Supabase whether the signature is
    good is a network round trip to learn something the secret in this process
    already proves. That round trip sat in front of EVERY authenticated
    request -- five of them for one tap of the match timer -- and from a
    serverless function each one costs tens to hundreds of milliseconds.

    Returns None when local verification is not possible (no secret, or a
    project signing with a key this cannot check), so the caller falls back to
    asking the server. Returns None is NOT the same as "invalid": a token that
    is present but fails signature or expiry raises, and must not be retried
    against the network.

    The trade-off, stated plainly: a token revoked mid-life stays acceptable
    here until it expires, where the network check would have caught it. Supabase
    access tokens are short-lived and the client refreshes them, so the window
    is the token's remaining lifetime -- an hour at the default. Sign-out clears
    the token on the device; it does not need the server to agree.
    """
    secret = settings.SUPABASE_JWT_SECRET
    if not secret:
        return None

    try:
        from jose import jwt as jose_jwt
        from jose.exceptions import JWTError
    except Exception:
        return None

    try:
        claims = jose_jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            # Supabase stamps every user token with this audience.
            audience="authenticated",
            options={"verify_aud": True, "verify_exp": True, "verify_signature": True},
        )
    except JWTError as e:
        text = str(e).lower()
        # An algorithm this secret cannot check at all (a project using
        # asymmetric signing keys) is "cannot verify here", not "bad token" --
        # fall back rather than locking every user out.
        if "algorithm" in text or "unsupported" in text:
            return None
        raise HTTPException(
            status_code=401,
            detail="Session expired or invalid. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception:
        return None

    if not claims.get("sub"):
        return None
    return _TokenUser(claims)


def get_current_user(token: str = Depends(get_access_token)):
    """
    Verify the caller's access token.

    Locally where the JWT secret allows it, otherwise by asking Supabase.

    The remote path runs on a per-request client. The module-level client is
    shared by every concurrent request and `auth.get_user()` mutates its
    session state, so verifying on it made overlapping requests interfere with
    each other -- which surfaced as intermittent 500s while the UI polled.
    """
    local = _verify_locally(token)
    if local is not None:
        return local

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
        "city": user_meta.get("city"),
    }


def _heal_profile(user, user_id: str) -> dict:
    """
    Create the missing profiles row, so the identity is real.

    An authenticated account with no profiles row is not merely inconvenient:
    a dozen columns reference profiles(id) -- boards.confirmed_by,
    matches.toss_recorded_by, tournaments.owner_id, tournament_access.user_id,
    audit_logs.user_id -- so the first action that records WHO DID IT fails with
    a foreign key violation. This used to hand back a profile assembled from
    token claims instead, which let the caller sign in, read every screen and
    run a whole tournament before failing at the board, mid-match, with a
    Postgres constraint name in a toast.

    The role is taken from app_metadata ONLY. user_metadata is writable by the
    account holder with nothing but the anon key the browser already ships, so
    trusting it here would let anyone mint themselves an admin profile. When
    app_metadata does not say, the healed row is a player: we genuinely do not
    know, and the safe assumption is the smaller one. Promote with
    db/promote_admin.py, or restore the real row with db/repair_profiles.py.
    """
    app_meta = _metadata(user, "app_metadata")
    user_meta = _metadata(user, "user_metadata")
    role = app_meta.get("role")
    row = {
        "id": user_id,
        "email": getattr(user, "email", None),
        "name": user_meta.get("name") or "User",
        "role": role if role in ("admin", "player") else "player",
        "rating": user_meta.get("rating") or 1500,
        "club": user_meta.get("club") or "Independent",
        "city": user_meta.get("city"),
    }
    try:
        created = get_admin_db().table("profiles").upsert(row).execute()
        if created.data:
            logger.warning(
                f"Created the missing profile row for {user_id} "
                f"(role={row['role']}) so their actions can be recorded."
            )
            return created.data[0]
    except Exception as e:
        logger.error(f"Could not create the missing profile for {user_id}: {str(e)}")

    raise HTTPException(
        status_code=409,
        detail=("Your account is not fully set up, so this action cannot be "
                "recorded. Ask an organiser to run db/repair_profiles.py."),
    )


def get_user_profile(user = Depends(get_current_user)):
    """
    The caller's profile row, created from their token claims if it is missing.

    Neither a missing row nor a failed read is answered with an invented
    identity any more. A missing row is repaired; a failed read is reported as
    a temporary failure, because degrading to token claims silently swapped the
    caller's role for whatever their metadata happened to say.
    """
    user_id = getattr(user, "id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Token carries no user id.")

    try:
        # Service client: this is an internal identity lookup, and it must not
        # depend on a SELECT policy being present for the caller's role.
        res = get_admin_db().table("profiles").select("*").eq("id", user_id).execute()
    except Exception as e:
        # Previously this fell through to the token claims, which meant a blip
        # in one query could hand someone a different role than the one their
        # profile row records. Say the truth instead: we cannot tell right now.
        logger.error(f"Profile lookup failed for {user_id}: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail="Could not read your profile just now. Please try again.",
        )

    if res.data:
        return res.data[0]

    logger.warning(f"No profile row for authenticated user {user_id}; creating one.")
    return _heal_profile(user, user_id)


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
