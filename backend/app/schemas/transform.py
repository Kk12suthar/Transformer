from pydantic import BaseModel


class TransformRequest(BaseModel):
    session_id: str
    query: str
    folder_id: str | None = None
    user_id: str | None = None
    chat_model: str | None = None


class ActivateRequest(BaseModel):
    session_id: str
    folder_id: str | None = None
