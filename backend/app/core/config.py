import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8100"))
    app_env: str = os.getenv("APP_ENV", "dev")
    app_cors_origins_raw: str = os.getenv(
        "APP_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    )

    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "change-me-for-production")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_expires_minutes: int = int(os.getenv("JWT_EXPIRES_MINUTES", "480"))
    api_key_encryption_key: str = os.getenv(
        "API_KEY_ENCRYPTION_KEY",
        os.getenv("JWT_SECRET_KEY", "change-me-for-production"),
    )

    pg_host: str = os.getenv("POSTGRES_HOST", "localhost")
    pg_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    pg_user: str = os.getenv("POSTGRES_USER", "postgres")
    pg_password: str = os.getenv("POSTGRES_PASSWORD", "imsuperuser")
    pg_dbname: str = os.getenv("POSTGRES_DBNAME", "mvp")
    pg_sslmode: str = os.getenv("POSTGRES_SSLMODE", "prefer")

    app_schema: str = os.getenv("APP_SCHEMA", "mvp")
    uploads_schema: str = os.getenv("UPLOADS_SCHEMA", "uploads")
    adk_schema_raw: str | None = os.getenv("ADK_SCHEMA")
    upload_max_bytes: int = int(os.getenv("UPLOAD_MAX_BYTES", str(25 * 1024 * 1024)))
    upload_max_rows: int = int(os.getenv("UPLOAD_MAX_ROWS", "100000"))
    data_retention_hours: int = int(os.getenv("DATA_RETENTION_HOURS", "24"))
    free_agent_messages: int = int(os.getenv("FREE_AGENT_MESSAGES", "5"))

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.app_cors_origins_raw.split(",") if o.strip()]

    @property
    def db_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_dbname}?sslmode={self.pg_sslmode}"
        )

    @property
    def adk_schema(self) -> str:
        # Isolate ADK tables by default so app schema migrations don't clash
        # with ADK's internal schema evolution.
        return self.adk_schema_raw or f"{self.app_schema}_adk"

    @property
    def adk_db_url(self) -> str:
        """
        Legacy DB URL for ADK's DatabaseSessionService.
        """
        base = (
            f"postgresql+psycopg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_dbname}"
        )
        return f"{base}?sslmode={self.pg_sslmode}&options=-c search_path%3D{self.adk_schema}"

    # Name used by ADK Runner + session service — change here to rename the app
    adk_app_name: str = os.getenv("ADK_APP_NAME", "mvp_transformation_stream")



settings = Settings()
