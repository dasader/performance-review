import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

import app.models  # noqa: F401  — 모든 모델 등록(FK 해석에 필요)
from app.config import settings
from app.database import SessionLocal, get_db
from app.routers import admin, public
from app.services.runner import loop
from app.services.visitors import record_visit

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

# 방문자 집계 제외 대상: 관리자 API, 헬스체크, 정적 자원(현재 백엔드는 정적 자원을
# 서비스하지 않지만 — nginx가 담당 — 방어적으로 남겨둔다).
_VISIT_EXCLUDED_PREFIXES = ("/api/admin", "/static")
_VISIT_EXCLUDED_PATHS = {"/api/health"}


def _client_ip(request: Request) -> str:
    # 프록시(nginx) 뒤이므로 X-Forwarded-For의 첫 값을 우선 쓴다.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


@app.middleware("http")
async def track_visitor(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if (
        path.startswith("/api/")
        and path not in _VISIT_EXCLUDED_PATHS
        and not any(path.startswith(p) for p in _VISIT_EXCLUDED_PREFIXES)
    ):
        # 테스트는 get_db를 override해 인메모리 sqlite를 쓴다. 그 override를 따르지
        # 않으면 이 미들웨어가 실제 DATABASE_URL(테스트에서는 존재하지 않는 postgres)에
        # 연결을 시도해 모든 공개 API 테스트가 깨진다.
        db_factory = request.app.dependency_overrides.get(get_db, SessionLocal)
        db = db_factory()
        try:
            # 동기 DB 쓰기를 미들웨어에서 그대로 부르면 잡 루프가 얹혀 있는 같은
            # 이벤트 루프가 그동안 멈춘다 — 공개 화면이 4~5초마다 폴링하므로 꾸준히
            # 발생한다. FastAPI가 동기 엔드포인트를 돌리는 것과 같은 스레드풀로 뺀다.
            await run_in_threadpool(
                record_visit, db, _client_ip(request), request.headers.get("user-agent", "")
            )
        finally:
            db.close()
    return response


@app.get("/api/health")
def health():
    return {"status": "ok"}
