import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import app.models  # noqa: F401  — 모든 모델 등록(FK 해석에 필요)
from app.services.runner import loop

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(loop())
    yield
    task.cancel()


app = FastAPI(title="전략기술 논문성과 분석", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok"}
