from datetime import datetime

from pydantic import BaseModel


class SessionCreateRequest(BaseModel):
    title: str | None = None


class SessionOut(BaseModel):
    id: str
    user_id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime


class SessionTableOut(BaseModel):
    id: str
    table_name: str
    table_role: str
    source_file_id: str | None
    created_at: datetime
