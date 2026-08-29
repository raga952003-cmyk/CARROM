import os
from pathlib import Path
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Resolve .env against the backend package root rather than the process working
# directory, so the server starts correctly no matter where it is launched from.
BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_DIR / ".env"

load_dotenv(ENV_FILE)

class Settings(BaseSettings):
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    SUPABASE_JWT_SECRET: str = os.getenv("SUPABASE_JWT_SECRET", "")
    
    API_PORT: int = int(os.getenv("PORT", 8000))
    API_ENV: str = os.getenv("ENV", "development")

    # Server-side only. The browser calls /api/ai/* instead, so the key is
    # never inlined into the frontend bundle.
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # Comma-separated list of allowed browser origins, used outside development.
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "")

    # Whether a tournament's owner is the only admin who may manage it.
    #
    # Off by default, because this deployment is run by one organiser and the
    # ownership check was blocking them from their own screens with "request
    # access to help run it" -- from an account that could not grant it.
    #
    # The tradeoff is real: with this off, ANY account holding the admin role
    # can manage, score and delete EVERY tournament in the database. Turn it on
    # (ENFORCE_TOURNAMENT_OWNERSHIP=true) before letting a second organisation
    # share this instance. It never affects players, who are refused either way.
    ENFORCE_TOURNAMENT_OWNERSHIP: bool = os.getenv(
        "ENFORCE_TOURNAMENT_OWNERSHIP", "false"
    ).strip().lower() in ("1", "true", "yes", "on")

    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    class Config:
        env_file = str(ENV_FILE)
        extra = "ignore"

settings = Settings()
