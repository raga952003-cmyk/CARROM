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


# Supabase's published signing keys, so an ES256/RS256 project is verified in
# this process instead of over the network.
#
# A Supabase project now signs access tokens with an asymmetric key by
# default. This module could only check HS256 against SUPABASE_JWT_SECRET, so
# on such a project every authenticated request fell through to
# client.auth.get_user() -- a real round trip to Supabase, measured at 916 ms
# against a 242 ms baseline, on EVERY request. Five of those for one tap of
# the match timer.
#
# The public keys are public: the endpoint needs no credentials. Cached, so the
# cost is one fetch per worker per TTL and nothing per request.
_JWKS: dict = {"keys": None, "at": 0.0}
_JWKS_TTL = 600.0          # a successful fetch is good for ten minutes
_JWKS_FAIL_TTL = 30.0      # a failed one is retried sooner, but not per request

# Which key material is allowed to verify which algorithm.
#
# This binding is the whole defence against algorithm confusion. The header is
# attacker-controlled, so if a public key from the JWKS could verify an HS256
# token, anyone could take the published key, HMAC a token of their choosing
# with it, and be believed. An HS* token is therefore only ever checked against
# the shared secret, and an asymmetric token only ever against a published
# public key. Neither can stand in for the other.
_SYMMETRIC = ("HS256", "HS384", "HS512")
_ASYMMETRIC = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512",
               "PS256", "PS384", "PS512")


def _fetch_jwks():
    """The project's JWKS, cached. [] means "asked and could not get them"."""
    import time
    now = time.monotonic()
    cached, at = _JWKS["keys"], _JWKS["at"]
    ttl = _JWKS_TTL if cached else _JWKS_FAIL_TTL
    if cached is not None and (now - at) < ttl:
        return cached

    base = (settings.SUPABASE_URL or "").strip().rstrip("/")
    if not base:
        _JWKS.update({"keys": [], "at": now})
        return []
    try:
        import httpx
        # Short timeout on purpose: this sits in front of a request, and
        # falling back to the network check is better than hanging.
        res = httpx.get(f"{base}/auth/v1/.well-known/jwks.json", timeout=3.0)
        res.raise_for_status()
        keys = (res.json() or {}).get("keys") or []
    except Exception as e:                                    # noqa: BLE001
        logger.info("Could not fetch JWKS from %s (%s); will verify remotely.",
                    base, e)
        _JWKS.update({"keys": [], "at": now})
        return []
    _JWKS.update({"keys": keys, "at": now})
    return keys


def _signing_key(header: dict):
    """(key, algorithm) this process may use for that header, or (None, None)."""
    alg = str(header.get("alg") or "").upper()

    if alg in _SYMMETRIC:
        secret = settings.SUPABASE_JWT_SECRET
        return (secret, alg) if secret else (None, None)

    if alg in _ASYMMETRIC:
        kid = header.get("kid")
        if not kid:
            return (None, None)
        keys = _fetch_jwks()
        match = next((k for k in keys if k.get("kid") == kid), None)
        if match is None and keys:
            # Keys rotate. One forced refresh before giving up, rather than
            # failing every request until the TTL expires.
            _JWKS.update({"keys": None, "at": 0.0})
            match = next((k for k in _fetch_jwks() if k.get("kid") == kid), None)
        return (match, alg) if match else (None, None)

    # "none", or something this does not know: not verifiable here.
    return (None, None)


def _verify_locally(token: str):
    """
    Verify the access token's signature with the project's JWT secret.

    Supabase signs these itself, so asking Supabase whether the signature is
    good is a network round trip to learn something the secret in this process
    already proves. That round trip sat in front of EVERY authenticated
    request -- five of them for one tap of the match timer -- and from a
    serverless function each one costs tens to hundreds of milliseconds.

    Returns the verified user on success, and None whenever this process
    cannot CONFIRM the token -- no secret, an algorithm it cannot check, a bad
    signature, an expired token. None means "ask Supabase", and Supabase's
    answer is final. Only a success here skips the round trip.

    The trade-off, stated plainly: a token revoked mid-life stays acceptable
    here until it expires, where the network check would have caught it. Supabase
    access tokens are short-lived and the client refreshes them, so the window
    is the token's remaining lifetime -- an hour at the default. Sign-out clears
    the token on the device; it does not need the server to agree.
    """
    try:
        from jose import jwt as jose_jwt
        from jose.exceptions import JWTError
    except Exception:
        return None

    try:
        header = jose_jwt.get_unverified_header(token)
    except Exception:
        return None

    # The key is chosen by the token's algorithm, from the one source allowed
    # to verify that algorithm. See _signing_key for why that binding matters.
    key, algorithm = _signing_key(header)
    if not key:
        return None

    try:
        claims = jose_jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            # Supabase stamps every user token with this audience.
            audience="authenticated",
            options={"verify_aud": True, "verify_exp": True, "verify_signature": True},
        )
    except JWTError as e:
        # "Cannot verify here" is not "invalid", and this could not tell them
        # apart -- so it locked whole deployments out.
        #
        # The intent was already written down: an algorithm this secret cannot
        # check (a project using asymmetric signing keys) should fall back to
        # asking Supabase rather than refusing every user. The detection never
        # fired. python-jose raises "The specified alg value is not allowed",
        # which contains "alg" but NOT "algorithm", so the test matched nothing
        # and the 401 below ran instead. Measured, with algorithms=["HS256"]:
        #
        #   wrong HS256 secret -> "Signature verification failed."
        #   ES256-signed token -> "The specified alg value is not allowed"
        #   RS256-signed token -> "The specified alg value is not allowed"
        #
        # and the old test returned False for all three. Supabase projects now
        # default to ECC (ES256) signing keys, so on such a project EVERY
        # authenticated request answered "Session expired or invalid. Please
        # sign in again." immediately after a correct sign-in. The client
        # clears its token on 401, so the request after that said "Missing
        # bearer access token" and the user was signed out on the spot. A
        # rotated or mistyped JWT secret did the same thing.
        #
        # Any local failure now falls back to the network check, which is
        # authoritative: if Supabase accepts the token it is good, and if it
        # does not, get_current_user raises 401 there. Only a local SUCCESS
        # short-circuits the round trip.
        #
        # The trade, stated plainly: a token this process cannot verify --
        # expired or tampered included -- now costs one round trip before it is
        # refused, where it used to be refused here. That is the right price
        # against signing out every user of a correctly configured deployment
        # because of the signing algorithm their project happens to use.
        logger.info(
            "Local JWT verification could not confirm this token (%s); "
            "asking Supabase instead.", e,
        )
        return None
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
        # 503, not 500. The server is not broken; it is unconfigured, and the
        # difference matters to whoever is looking at the log: one is a bug to
        # find, the other is a variable to set. Carries the variable name.
        raise HTTPException(status_code=503, detail=str(e))

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
