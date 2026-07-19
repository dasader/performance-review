import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.models  # noqa: F401  — 모든 모델 등록(FK 해석에 필요)
from app.config import settings
from app.routers import admin, public
from app.services.runner import loop

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(loop())
    yield
    task.cancel()


app = FastAPI(title="전략기술 논문성과 분석", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # M16: 관리자 인증이 정적 헤더 하나뿐이라 전체 오리진 허용은 위험하다.
    # CORS_ORIGINS(.env, 쉼표 구분)로 좁힌다.
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(public.router)
app.include_router(admin.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
