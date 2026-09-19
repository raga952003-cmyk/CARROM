import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import (
    auth,
    players,
    tournaments,
    matches,
    notifications,
    imports,
    payments,
    registrations,
    teams,
    access,
    ai,
    fixtures,
    scheduling,
    standings,
    audit,
)

app = FastAPI(
    title="Carrom Arena API",
    description="AICF Standard Serverless Tournament Engine for Carrom Arena",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json"
)

# CORS configurations
# Wildcard origins are only safe for local development. Outside development the
# allowed origins must be listed explicitly in CORS_ORIGINS, because
# allow_credentials=True with "*" would let any site call the API with the
# user's credentials.
if settings.API_ENV == "development":
    allowed_origins = ["*"]
else:
    allowed_origins = settings.cors_origin_list()

# Logging, configured here rather than left to whoever imports us.
#
# The loggers throughout this app are named "uvicorn.error", which has handlers
# only when uvicorn configured logging -- true locally (run.py), false on
# Vercel, where api/index.py hands the ASGI app to the platform runtime. With
# no handler and no level, everything below WARNING was silently dropped in
# production, including the reconciliation lines that say a payment was
# deliberately not settled. Those are exactly the messages you go looking for
# when money is missing.
if not logging.getLogger("uvicorn.error").handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

# Two configurations that are never intentional, said out loud at startup.
#
# Neither is an error the app can resolve for itself, and neither should stop
# it serving -- a refusal to boot over CORS would take a whole deployment down
# for a variable that a redeploy can fix. So they are logged, and reported by
# /api/health, where a person can see them without reading logs at all.
_startup_logger = logging.getLogger("uvicorn.error")
if settings.API_ENV == "development":
    _startup_logger.warning(
        "CORS is wide open: ENV is '%s', so any origin may call this API with "
        "credentials. Correct for local development; set ENV=production on a "
        "deployed host.", settings.API_ENV,
    )
elif not allowed_origins:
    _startup_logger.error(
        "ENV is '%s' but CORS_ORIGINS is empty, so NO browser origin is "
        "permitted and every request from the site will fail. Set CORS_ORIGINS "
        "to the deployed origin.", settings.API_ENV,
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all endpoint domain routers
app.include_router(auth.router, prefix="/api")
app.include_router(players.router, prefix="/api")
app.include_router(tournaments.router, prefix="/api")
app.include_router(registrations.router, prefix="/api")
app.include_router(teams.router, prefix="/api")
app.include_router(access.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(matches.router, prefix="/api")
app.include_router(fixtures.router, prefix="/api")
app.include_router(scheduling.router, prefix="/api")
app.include_router(standings.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(payments.router, prefix="/api")

@app.get("/")
async def root():
    return {"message": "Welcome to the Carrom Arena Tournament Engine API."}

# Cached migration state. None until probed; [] means everything is applied.
# Every answer expires after _PENDING_RECHECK_SECONDS, the green one included
# -- see health() for why "all applied" is not safe to keep for good.
_pending_cache = None
_pending_checked_at = 0.0
_PENDING_RECHECK_SECONDS = 30

# Migrations probed by selecting a column they add -- or, for 011, the view
# they create, which PostgREST serves exactly like a table. Each entry is
# (migration, table or view, column). 007 replaces a function rather than
# adding a column and is probed separately, by rpc, in health() below.
_COLUMN_PROBES = (
    ("002_serverless_architecture", "idempotency_keys", "key"),
    ("003_ownership_and_access", "tournament_access", "id"),
    ("004_match_toss", "matches", "toss_choice"),
    ("005_board_detail", "boards", "board_winner"),
    ("006_sets_and_sides", "boards", "set_number"),
    ("010_walkover", "matches", "walkover_by"),
    ("011_profile_privacy", "public_profiles", "id"),
    ("012_lifecycle", "tournaments", "champion_id"),
    ("015_payments", "payments", "razorpay_order_id"),
)

# Migrations that leave nothing PostgREST can see. Reporting one of these as
# applied would be a guess, and reporting it as pending would never clear, so
# they are named separately with the reason. The deploy checklist applies
# these by hand and reads each one's RAISE NOTICE in the SQL editor instead.
UNPROBEABLE_MIGRATIONS = (
    {"migration": "008_drop_city_default",
     "reason": "only drops the DEFAULT on profiles.city; PostgREST does not "
               "expose column defaults, and the one query that would show it "
               "is an insert."},
    {"migration": "009_stop_timer_on_finish",
     "reason": "installs a BEFORE UPDATE trigger on matches and adds no column; "
               "its function returns TRIGGER, which PostgREST leaves out of the "
               "rpc surface, so its absence looks the same as its presence."},
    {"migration": "013_profiles_trigger_and_rls",
     "reason": "a trigger on auth.users and RLS policies, none of it visible "
               "through PostgREST; is_admin() can be called, but every earlier "
               "version of triggers_and_security.sql created it too, so it "
               "proves nothing about the trigger or the policies."},
    {"migration": "014_lock_profile_role",
     "reason": "a REVOKE, a BEFORE UPDATE trigger on profiles and a rewritten "
               "policy. PostgREST exposes neither grants nor triggers, and the "
               "one query that would show the revoke is an update to somebody "
               "else's role -- which is the thing it exists to prevent. Verify "
               "it by reading its RAISE NOTICE in the SQL editor."},
    {"migration": "016_payment_ledger_integrity",
     "reason": "a partial UNIQUE INDEX, two BEFORE DELETE triggers and a "
               "GRANT, and no column. PostgREST exposes tables and columns, "
               "never indexes, triggers or grants, and the queries that would "
               "prove them are a second paid insert and a tournament delete "
               "-- the double charge and the lost ledger it exists to "
               "prevent. Verify it by reading its RAISE NOTICEs in the SQL "
               "editor."},
)


def _health_payload(pending, rpc_state, idem_state, owner_state,
                    client_ok, admin_ok):
    """The health response, so the cached path returns the same shape."""
    return {
        "status": "ok" if not pending else "degraded",
        "pending_migrations": pending,
        "migrations": (
            "all applied" if not pending
            else "DEGRADED - apply: " + ", ".join("db/migrations/%s.sql" % m for m in pending)
        ),
        "env": settings.API_ENV,
        "database_client": client_ok,
        "database_admin_client": admin_ok,
        "transactional_writes": (
            "unknown (not exercised yet)" if rpc_state is None
            else "atomic" if rpc_state
            else "DEGRADED - apply db/migrations/002_serverless_architecture.sql"
        ),
        "idempotency": (
            "unknown (not exercised yet)" if idem_state is None
            else "active" if idem_state
            else "DEGRADED - apply db/migrations/002_serverless_architecture.sql"
        ),
        "tournament_ownership": (
            "unknown (not exercised yet)" if owner_state is None
            else "enforced" if owner_state
            else "DEGRADED - any admin can manage any tournament; "
                 "apply db/migrations/003_ownership_and_access.sql"
        ),
        # Constant, so the cached paths carry it too: it describes what the
        # probe can see, not what the database holds.
        "unprobeable_migrations": [dict(m) for m in UNPROBEABLE_MIGRATIONS],
        # Which Razorpay mode this deployment is in, said out loud.
        #
        # Razorpay has no mode flag -- test and live differ only by the key
        # prefix -- so without this the only way to find out which one a
        # deployment is using is to make a payment and see whether real money
        # moves. "test" on a production host, or "live" on a staging one, is
        # then something a person can notice before a player does.
        "payments": _payments_state(),
        # The resolved CORS posture, so a misconfiguration is visible without
        # reading startup logs. "open" on a deployed host, or "blocking-all"
        # anywhere, is a deployment that needs a variable changed.
        "cors": _cors_state(),
    }


def _cors_state():
    if settings.API_ENV == "development":
        return {"mode": "open", "origins": ["*"],
                "detail": "ENV=development, so any origin may call this API. "
                          "Set ENV=production on a deployed host."}
    if not allowed_origins:
        return {"mode": "blocking-all", "origins": [],
                "detail": "DEGRADED - ENV is not development and CORS_ORIGINS is "
                          "empty, so no browser origin is permitted."}
    return {"mode": "restricted", "origins": list(allowed_origins)}


def _payments_state():
    from app.services import razorpay_client

    if not razorpay_client.razorpay_configured():
        return {"provider": "razorpay", "configured": False, "mode": "off",
                "detail": "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set; "
                          "entry fees cannot be collected online."}

    return {
        "provider": "razorpay",
        "configured": True,
        "mode": "live" if razorpay_client.is_live_mode() else "test",
        "webhook": ("configured" if razorpay_client.webhook_configured()
                    else "DEGRADED - RAZORPAY_WEBHOOK_SECRET not set; a payment "
                         "whose browser callback is lost will not be recorded"),
    }


@app.get("/api/health")
async def health():
    """
    Readiness probe (spec 88). Reports client configuration, whether the
    transactional RPCs from migration 002 are present, which migrations are
    missing (pending_migrations), and which ones it cannot see at all
    (unprobeable_migrations). A deploy gates on status "ok" with nothing
    pending; see .github/workflows/ci.yml.
    """
    from app.database import supabase_client, supabase_admin
    from app.services.transaction_service import transactional_rpc_available
    from app.utils.idempotency import idempotency_store_available
    from app.services.access_control import ownership_enforced

    rpc_state = transactional_rpc_available()
    idem_state = idempotency_store_available()
    owner_state = ownership_enforced()

    # Which migrations are actually present. Every feature here degrades rather
    # than crashing when its migration is missing, which is right in the middle
    # of a tournament and wrong at deploy time: without this the app comes up
    # green while quietly not recording tosses or board detail.
    # Cached, because a schema does not change without a deployment.
    #
    # These probes are ten sequential Supabase round trips, and from a
    # serverless function each costs a couple of hundred milliseconds: /health
    # was measured at 2.2 seconds to return about nothing. Anything polling it
    # paid that every time. A positive result is kept for the life of the
    # process; a negative one is re-checked, so applying a migration takes
    # effect without a redeploy.
    global _pending_cache, _pending_checked_at
    now = time.monotonic()
    # "All applied" was cached for the life of the instance, on the reasoning
    # that a schema cannot regress without a deployment. True of the schema,
    # false of the ANSWER: the probe can only see the columns it selects, and
    # a migration added to _COLUMN_PROBES after this instance warmed up was
    # never asked about. During a cutover -- paste 015 and 016, then check
    # health -- a warm instance kept answering "all applied" from before the
    # probes existed, which is the one moment the answer is load-bearing.
    #
    # Given the same expiry as any other cached answer. Re-probing every
    # thirty seconds costs ten selects on a route nobody calls in a loop.
    if (_pending_cache == [] and _pending_checked_at
            and now - _pending_checked_at < _PENDING_RECHECK_SECONDS):
        return _health_payload([], rpc_state, idem_state, owner_state,
                               supabase_client is not None, supabase_admin is not None)
    if _pending_cache is not None and now - _pending_checked_at < _PENDING_RECHECK_SECONDS:
        return _health_payload(_pending_cache, rpc_state, idem_state, owner_state,
                               supabase_client is not None, supabase_admin is not None)

    pending = []
    if supabase_admin is not None:
        for migration, table, column in _COLUMN_PROBES:
            try:
                supabase_admin.table(table).select(column).limit(1).execute()
            except Exception:
                pending.append(migration)

        # 007 replaces a function rather than adding a column, so it is probed
        # by argument list: the old six-argument version cannot take a set.
        try:
            supabase_admin.rpc("apply_board_result", {
                "p_match_id": "00000000-0000-0000-0000-000000000000",
                "p_board_number": 0, "p_board_patch": {}, "p_match_patch": {},
                "p_audit": {}, "p_next_board_number": None, "p_set_number": 1,
            }).execute()
        except Exception as e:
            # "board_not_found" means the seven-argument version ran and simply
            # found nothing, which is exactly what a zero UUID should do.
            if "board_not_found" not in str(e) and "insufficient_privilege" not in str(e):
                pending.append("007_apply_board_result_sets")

    _pending_cache = pending
    _pending_checked_at = time.monotonic()
    return _health_payload(pending, rpc_state, idem_state, owner_state,
                           supabase_client is not None, supabase_admin is not None)
