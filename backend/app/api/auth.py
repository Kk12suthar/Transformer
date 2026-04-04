import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.database import get_db
from app.schemas.auth import AuthResponse, SigninRequest, SignupRequest, UserOut


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/signup", response_model=AuthResponse)
def signup(payload: SignupRequest, db: Session = Depends(get_db)) -> AuthResponse:
    exists_q = text(
        f"SELECT 1 FROM {settings.app_schema}.users WHERE email = :email LIMIT 1"
    )
    if db.execute(exists_q, {"email": payload.email}).first():
        raise HTTPException(status_code=409, detail="Email already exists")

    user_id = uuid.uuid4().hex
    insert_q = text(
        f"""
        INSERT INTO {settings.app_schema}.users (id, email, password_hash, full_name)
        VALUES (:id, :email, :password_hash, :full_name)
        """
    )
    db.execute(
        insert_q,
        {
            "id": user_id,
            "email": payload.email,
            "password_hash": hash_password(payload.password),
            "full_name": payload.full_name,
        },
    )
    db.commit()

    token = create_access_token({"sub": user_id, "email": payload.email})
    return AuthResponse(
        success=True,
        message="Account created",
        access_token=token,
        user=UserOut(id=user_id, email=payload.email, full_name=payload.full_name),
    )


@router.post("/signin", response_model=AuthResponse)
def signin(payload: SigninRequest, db: Session = Depends(get_db)) -> AuthResponse:
    q = text(
        f"""
        SELECT id, email, full_name, password_hash
        FROM {settings.app_schema}.users
        WHERE email = :email
        LIMIT 1
        """
    )
    row = db.execute(q, {"email": payload.email}).mappings().first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    if not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    token = create_access_token({"sub": row["id"], "email": row["email"]})
    return AuthResponse(
        success=True,
        message="Signed in",
        access_token=token,
        user=UserOut(id=row["id"], email=row["email"], full_name=row["full_name"]),
    )
