from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models  # noqa: F401  (register models)
from app.config import settings
from app.database import Base, engine
from app.routers import jobs


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # simple bootstrap; use Alembic migrations in production
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Bulk Certificate Generator", version="1.0.0", lifespan=lifespan)
app.include_router(jobs.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
