from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_access_token
from app.db.database import get_db


security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> dict:
    token_data = decode_access_token(credentials.credentials)
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user_id = token_data.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token is missing subject")

    q = text(
        f"""
        SELECT id, email, full_name
        FROM {settings.app_schema}.users
        WHERE id = :user_id
        """
    )
    row = db.execute(q, {"user_id": user_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=401, detail="User not found")

    return {"id": row["id"], "email": row["email"], "full_name": row["full_name"]}
