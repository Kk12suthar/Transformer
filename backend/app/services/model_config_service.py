import base64
import json
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings


SUPPORTED_PROVIDERS = ("google", "openai", "anthropic")
TRANSFORM_AGENT_ROLES = (
    "orchestrator",
    "search_agent",
    "analysis_agent",
    "operations_agent",
)
DEFAULT_MODELS = [{"model_name": "gemini-2.5-flash", "model_type": "google"}]


@dataclass
class ChatModelConfig:
    provider_api_keys: dict[str, str]
    all_models: list[dict[str, str]]
    selected_model: str


def _build_fernet() -> Fernet:
    key_material = settings.api_key_encryption_key.encode()[:32].ljust(32, b"\0")
    encoded_key = base64.urlsafe_b64encode(key_material)
    return Fernet(encoded_key)


_cipher = _build_fernet()


def encrypt_api_key(raw_value: str) -> str:
    return _cipher.encrypt(raw_value.encode()).decode()


def decrypt_api_key(encrypted_value: str | None) -> str:
    if not encrypted_value:
        return ""
    try:
        return _cipher.decrypt(encrypted_value.encode()).decode()
    except (InvalidToken, ValueError):
        # Backward compatibility: allow plaintext values created before encryption.
        if not encrypted_value.startswith("gAAAAA"):
            return encrypted_value
        return ""


def _mask_api_key(value: str) -> str:
    if not value:
        return ""
    visible = value[-4:] if len(value) >= 4 else value
    return f"...{visible}"


def _normalize_models(raw_models: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    incoming = raw_models or []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()

    for model in incoming:
        name = str(model.get("model_name", "")).strip()
        provider = str(model.get("model_type", "")).strip().lower()
        if not name or provider not in SUPPORTED_PROVIDERS:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"model_name": name, "model_type": provider})

    return normalized or [dict(DEFAULT_MODELS[0])]


def _normalize_selected_model(selected_model: str | None, models: list[dict[str, str]]) -> str:
    allowed = {item["model_name"] for item in models}
    if selected_model and selected_model in allowed:
        return selected_model
    return models[0]["model_name"]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            return []
    return []


def _is_encrypted(value: str) -> bool:
    return value.startswith("gAAAAA")


def _resolve_provider_key(
    *,
    incoming_value: str,
    existing_encrypted: str,
) -> str:
    value = incoming_value.strip()
    if not value:
        return ""
    if value == "****" or value.startswith("..."):
        if existing_encrypted and not _is_encrypted(existing_encrypted):
            return encrypt_api_key(existing_encrypted)
        return existing_encrypted
    if _is_encrypted(value):
        return value
    return encrypt_api_key(value)


def _get_db_config_row(db: Session, user_id: str) -> dict[str, Any] | None:
    query = text(
        f"""
        SELECT provider_api_keys, all_models, selected_model
        FROM {settings.app_schema}.user_model_configs
        WHERE user_id = :user_id
        """
    )
    return db.execute(query, {"user_id": user_id}).mappings().first()


def get_chat_model_config(db: Session, user_id: str) -> ChatModelConfig:
    row = _get_db_config_row(db, user_id)
    provider_api_keys = {provider: "" for provider in SUPPORTED_PROVIDERS}
    all_models = [dict(DEFAULT_MODELS[0])]
    selected_model = DEFAULT_MODELS[0]["model_name"]

    if row:
        stored_keys = _as_dict(row["provider_api_keys"])
        for provider in SUPPORTED_PROVIDERS:
            raw = str(stored_keys.get(provider, "") or "")
            provider_api_keys[provider] = raw
        all_models = _normalize_models(_as_list(row["all_models"]))
        selected_model = _normalize_selected_model(
            row.get("selected_model"),
            all_models,
        )

    return ChatModelConfig(
        provider_api_keys=provider_api_keys,
        all_models=all_models,
        selected_model=selected_model,
    )


def get_chat_model_config_masked(db: Session, user_id: str) -> ChatModelConfig:
    raw_config = get_chat_model_config(db, user_id)
    masked_keys = {}
    for provider, encrypted_value in raw_config.provider_api_keys.items():
        masked_keys[provider] = _mask_api_key(decrypt_api_key(encrypted_value))

    return ChatModelConfig(
        provider_api_keys=masked_keys,
        all_models=raw_config.all_models,
        selected_model=raw_config.selected_model,
    )


def upsert_chat_model_config(
    *,
    db: Session,
    user_id: str,
    provider_keys_input: dict[str, str],
    all_models_input: list[dict[str, Any]] | None,
    selected_model_input: str | None,
) -> ChatModelConfig:
    existing = get_chat_model_config(db, user_id)

    merged_provider_keys = dict(existing.provider_api_keys)
    for provider, incoming in provider_keys_input.items():
        if provider not in SUPPORTED_PROVIDERS:
            continue
        merged_provider_keys[provider] = _resolve_provider_key(
            incoming_value=incoming,
            existing_encrypted=existing.provider_api_keys.get(provider, ""),
        )

    merged_models = _normalize_models(all_models_input or existing.all_models)
    selected_model = _normalize_selected_model(
        selected_model_input or existing.selected_model,
        merged_models,
    )

    query = text(
        f"""
        INSERT INTO {settings.app_schema}.user_model_configs
            (user_id, provider_api_keys, all_models, selected_model, created_at, updated_at)
        VALUES
            (:user_id, CAST(:provider_api_keys AS jsonb), CAST(:all_models AS jsonb), :selected_model, NOW(), NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            provider_api_keys = EXCLUDED.provider_api_keys,
            all_models = EXCLUDED.all_models,
            selected_model = EXCLUDED.selected_model,
            updated_at = NOW()
        """
    )
    db.execute(
        query,
        {
            "user_id": user_id,
            "provider_api_keys": json.dumps(merged_provider_keys),
            "all_models": json.dumps(merged_models),
            "selected_model": selected_model,
        },
    )
    db.commit()

    return get_chat_model_config_masked(db, user_id)


def build_runtime_agent_config(
    *,
    config: ChatModelConfig,
    requested_model: str | None = None,
) -> dict[str, Any]:
    selected = _normalize_selected_model(requested_model or config.selected_model, config.all_models)

    runtime_models: list[dict[str, str]] = []
    for item in config.all_models:
        provider = item["model_type"]
        encrypted_key = config.provider_api_keys.get(provider, "")
        runtime_models.append(
            {
                "model_name": item["model_name"],
                "model_type": provider,
                "model_api_key": decrypt_api_key(encrypted_key),
            }
        )

    return {
        "all_models": runtime_models,
        "default_model": selected,
        "transformation": {role: selected for role in TRANSFORM_AGENT_ROLES},
    }
