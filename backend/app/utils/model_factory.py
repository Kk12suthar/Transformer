import os
from typing import Any

def _infer_provider(model_name: str) -> str:
    lowered = model_name.lower()
    if lowered.startswith("openai/") or lowered.startswith("gpt-"):
        return "openai"
    if lowered.startswith("anthropic/") or lowered.startswith("claude"):
        return "anthropic"
    return "google"


def _normalize_provider(value: str, model_name: str) -> str:
    provider = (value or "").strip().lower()
    if provider in {"google", "openai", "anthropic"}:
        return provider
    return _infer_provider(model_name)


def _env_key_for_provider(provider: str) -> str:
    if provider == "google":
        return (
            os.getenv("GEMINI_API_KEY", "").strip()
            or os.getenv("GOOGLE_API_KEY", "").strip()
            or os.getenv("GOOGLE_GENAI_API_KEY", "").strip()
        )
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY", "").strip()
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY", "").strip()
    return ""


def _pick_model_name(app_config: dict[str, Any], section: str, key: str) -> str:
    section_cfg = app_config.get(section, {})
    if isinstance(section_cfg, dict) and section_cfg.get(key):
        return str(section_cfg[key])

    alt_section = app_config.get(f"{section}_agents", {})
    if isinstance(alt_section, dict) and alt_section.get(key):
        return str(alt_section[key])

    default_model = str(app_config.get("default_model", "")).strip()
    if default_model:
        return default_model

    env_model = os.getenv("MODEL_NAME", "").strip()
    if env_model:
        return env_model

    return "gemini-2.5-flash"


def _find_model_config(app_config: dict[str, Any], model_name: str) -> dict[str, Any]:
    all_models = app_config.get("all_models", [])
    if not isinstance(all_models, list):
        return {}
    for item in all_models:
        if not isinstance(item, dict):
            continue
        if str(item.get("model_name", "")).strip() == model_name:
            return item
    return {}


def _create_google_model(model_name: str, api_key: str):
    from google.adk.models.google_llm import Gemini
    from google.genai import Client

    if api_key:
        return Gemini(model=model_name, api_client=Client(api_key=api_key))
    raise ValueError(
        f"No API key configured for Google model '{model_name}'. "
        "Set it from Agent Panel."
    )


def _create_openai_model(model_name: str, api_key: str):
    from google.adk.models.lite_llm import LiteLlm

    if not api_key:
        raise ValueError(
            f"No API key configured for OpenAI model '{model_name}'. "
            "Set it from Agent Panel."
        )
    litellm_name = model_name if model_name.startswith("openai/") else f"openai/{model_name}"
    return LiteLlm(model=litellm_name, api_key=api_key)


def _create_anthropic_model(model_name: str, api_key: str):
    from google.adk.models.lite_llm import LiteLlm

    if not api_key:
        raise ValueError(
            f"No API key configured for Anthropic model '{model_name}'. "
            "Set it from Agent Panel."
        )
    litellm_name = (
        model_name if model_name.startswith("anthropic/") else f"anthropic/{model_name}"
    )
    return LiteLlm(model=litellm_name, api_key=api_key)


def create_model(model_name: str, model_type: str, api_key: str):
    provider = _normalize_provider(model_type, model_name)
    if provider == "google":
        return _create_google_model(model_name, api_key)
    if provider == "openai":
        return _create_openai_model(model_name, api_key)
    if provider == "anthropic":
        return _create_anthropic_model(model_name, api_key)
    raise ValueError(f"Unsupported model provider: {provider}")


def create_model_from_config(app_config: dict[str, Any], section: str, key: str):
    model_name = _pick_model_name(app_config, section, key)
    model_cfg = _find_model_config(app_config, model_name)
    provider = _normalize_provider(str(model_cfg.get("model_type", "")), model_name)

    configured_key = str(model_cfg.get("model_api_key", "") or "").strip()
    api_key = configured_key or _env_key_for_provider(provider)

    return create_model(model_name, provider, api_key)
