import logging
from supabase import create_client, Client
from app.config import settings

logger = logging.getLogger("uvicorn.error")

supabase_client: Client = None
supabase_admin: Client = None

if not settings.SUPABASE_URL:
    logger.warning("SUPABASE_URL env variable is missing. Database connection disabled.")
else:
    if settings.SUPABASE_ANON_KEY:
        supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
        logger.info("Supabase standard client initialized successfully.")
    else:
        logger.warning("SUPABASE_ANON_KEY is missing.")

    if settings.SUPABASE_SERVICE_ROLE_KEY:
        supabase_admin = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
        logger.info("Supabase service role admin client initialized successfully.")
    else:
        logger.warning("SUPABASE_SERVICE_ROLE_KEY is missing. Admin operations might fail.")

def get_db():
    """Returns the standard user-authenticated Supabase client"""
    return supabase_client

def get_admin_db():
    """Returns the service_role Supabase client that bypasses RLS"""
    if supabase_admin is None:
        raise ValueError("Supabase Admin client not configured.")
    return supabase_admin

def get_user_client(access_token: str) -> Client:
    """
    Returns a Supabase client bound to the caller's JWT.

    The module-level `supabase_client` authenticates as the `anon` role, so
    `auth.uid()` is NULL inside Postgres and every user-scoped RLS policy is
    inert. Binding the caller's token makes those policies evaluate correctly.
    A fresh client is built per request because the underlying PostgREST
    session stores the auth header on the instance, which would otherwise leak
    one user's token into another user's concurrent request.
    """
    if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
        raise ValueError("Supabase client not configured.")

    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    client.postgrest.auth(access_token)
    return client
