from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.schemas.model_config import ChatModelConfigResponse, ChatModelConfigUpdate
from app.services.model_config_service import (
    get_chat_model_config,
    get_chat_model_config_masked,
    get_free_message_quota,
    upsert_chat_model_config,
)


router = APIRouter(prefix="/api/v1/model-config", tags=["model-config"])


def _response_with_quota(db: Session, user_id: str) -> ChatModelConfigResponse:
    config = get_chat_model_config_masked(db, user_id)
    raw_config = get_chat_model_config(db, user_id)
    quota = get_free_message_quota(db=db, user_id=user_id, config=raw_config)
    return ChatModelConfigResponse(
        provider_keys=config.provider_api_keys,
        all_models=config.all_models,
        selected_model=config.selected_model,
        free_messages_used=quota.used,
        free_messages_limit=quota.limit,
        free_messages_remaining=quota.remaining,
        requires_api_key=quota.requires_api_key,
    )


@router.get("/chat", response_model=ChatModelConfigResponse)
def get_chat_model_settings(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> ChatModelConfigResponse:
    return _response_with_quota(db, user["id"])


@router.put("/chat", response_model=ChatModelConfigResponse)
def update_chat_model_settings(
    payload: ChatModelConfigUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> ChatModelConfigResponse:
    upsert_chat_model_config(
        db=db,
        user_id=user["id"],
        provider_keys_input=payload.provider_keys,
        all_models_input=[model.model_dump() for model in payload.all_models],
        selected_model_input=payload.selected_model,
    )
    return _response_with_quota(db, user["id"])
