from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, model_config, sessions, tables, transform, upload
from app.core.config import settings
from app.db.schema import init_schema


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_schema()
    yield


app = FastAPI(
    title="MVP Data Cleaning API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(upload.router)
app.include_router(tables.router)
app.include_router(transform.router)
app.include_router(model_config.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
