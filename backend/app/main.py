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

@app.get("/")
async def root():
    return {"message": "Welcome to the Carrom Arena Tournament Engine API."}

@app.get("/api/health")
async def health():
    """
    Readiness probe (spec 88). Reports client configuration and whether the
    transactional RPCs from migration 002 are present.
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
    pending = []
    if supabase_admin is not None:
        probes = (
            ("002_serverless_architecture", "idempotency_keys", "key"),
            ("003_ownership_and_access", "tournament_access", "id"),
            ("004_match_toss", "matches", "toss_choice"),
            ("005_board_detail", "boards", "board_winner"),
            ("006_sets_and_sides", "boards", "set_number"),
        )
        for migration, table, column in probes:
            try:
                supabase_admin.table(table).select(column).limit(1).execute()
            except Exception:
                pending.append(migration)

    return {
        "status": "ok" if not pending else "degraded",
        "pending_migrations": pending,
        "migrations": (
            "all applied" if not pending
            else "DEGRADED - apply: " + ", ".join("db/migrations/%s.sql" % m for m in pending)
        ),
        "env": settings.API_ENV,
        "database_client": supabase_client is not None,
        "database_admin_client": supabase_admin is not None,
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
    }
