from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.schemas.model_config import ChatModelConfigResponse, ChatModelConfigUpdate
from app.services.model_config_service import (
    get_chat_model_config_masked,
    upsert_chat_model_config,
)


router = APIRouter(prefix="/api/v1/model-config", tags=["model-config"])


@router.get("/chat", response_model=ChatModelConfigResponse)
def get_chat_model_settings(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> ChatModelConfigResponse:
    config = get_chat_model_config_masked(db, user["id"])
    return ChatModelConfigResponse(
        provider_keys=config.provider_api_keys,
        all_models=config.all_models,
        selected_model=config.selected_model,
    )


@router.put("/chat", response_model=ChatModelConfigResponse)
def update_chat_model_settings(
    payload: ChatModelConfigUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> ChatModelConfigResponse:
    config = upsert_chat_model_config(
        db=db,
        user_id=user["id"],
        provider_keys_input=payload.provider_keys,
        all_models_input=[model.model_dump() for model in payload.all_models],
        selected_model_input=payload.selected_model,
    )
    return ChatModelConfigResponse(
        provider_keys=config.provider_api_keys,
        all_models=config.all_models,
        selected_model=config.selected_model,
    )
