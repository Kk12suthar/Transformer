from pydantic import BaseModel, Field, field_validator


class ModelEntry(BaseModel):
    model_name: str
    model_type: str

    @field_validator("model_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("model_name is required")
        if len(name) > 120:
            raise ValueError("model_name is too long")
        return name

    @field_validator("model_type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        provider = value.strip().lower()
        if not provider:
            raise ValueError("model_type string is required")
        return provider


class ChatModelConfigUpdate(BaseModel):
    provider_keys: dict[str, str] = Field(default_factory=dict)
    all_models: list[ModelEntry] = Field(default_factory=list)
    selected_model: str | None = None

    @field_validator("provider_keys")
    @classmethod
    def validate_provider_keys(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, api_key in value.items():
            provider = key.strip().lower()
            if not provider:
                continue
            normalized[provider] = (api_key or "").strip()
        return normalized

    @field_validator("selected_model")
    @classmethod
    def normalize_selected_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        selected = value.strip()
        return selected or None


class ChatModelConfigResponse(BaseModel):
    provider_keys: dict[str, str] = Field(default_factory=dict)
    all_models: list[ModelEntry] = Field(default_factory=list)
    selected_model: str | None = None
    free_messages_used: int = 0
    free_messages_limit: int = 5
    free_messages_remaining: int = 5
    requires_api_key: bool = False
