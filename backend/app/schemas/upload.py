from pydantic import BaseModel


class UploadResponse(BaseModel):
    success: bool
    session_id: str
    file_id: str
    table_name: str
    message: str
