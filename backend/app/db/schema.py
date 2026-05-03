from sqlalchemy import text

from app.core.config import settings
from app.db.database import engine


def init_schema() -> None:
    app_schema = settings.app_schema
    uploads_schema = settings.uploads_schema

    ddl = f"""
    CREATE SCHEMA IF NOT EXISTS {app_schema};
    CREATE SCHEMA IF NOT EXISTS {uploads_schema};

    CREATE TABLE IF NOT EXISTS {app_schema}.users (
      id TEXT PRIMARY KEY,
      email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      full_name TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS {app_schema}.chat_sessions (
      id TEXT PRIMARY KEY,
      user_id TEXT NOT NULL REFERENCES {app_schema}.users(id) ON DELETE CASCADE,
      title TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'ACTIVE',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS {app_schema}.uploaded_files (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL REFERENCES {app_schema}.chat_sessions(id) ON DELETE CASCADE,
      original_name TEXT NOT NULL,
      stored_name TEXT NOT NULL,
      table_name TEXT NOT NULL,
      uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS {app_schema}.session_tables (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL REFERENCES {app_schema}.chat_sessions(id) ON DELETE CASCADE,
      table_name TEXT NOT NULL,
      table_role TEXT NOT NULL,
      source_file_id TEXT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS {app_schema}.chat_messages (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL REFERENCES {app_schema}.chat_sessions(id) ON DELETE CASCADE,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS {app_schema}.user_model_configs (
      user_id TEXT PRIMARY KEY REFERENCES {app_schema}.users(id) ON DELETE CASCADE,
      provider_api_keys JSONB NOT NULL DEFAULT '{{}}'::jsonb,
      all_models JSONB NOT NULL DEFAULT '[]'::jsonb,
      selected_model TEXT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS {app_schema}.user_agent_usage (
      user_id TEXT PRIMARY KEY REFERENCES {app_schema}.users(id) ON DELETE CASCADE,
      free_messages_used INTEGER NOT NULL DEFAULT 0,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """

    with engine.begin() as conn:
        for stmt in ddl.split(";"):
            sql = stmt.strip()
            if sql:
                conn.execute(text(sql))
