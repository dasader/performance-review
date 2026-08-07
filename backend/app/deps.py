import secrets

from fastapi import Header, HTTPException

from app.config import settings


def require_admin(x_admin_key: str = Header(default="")) -> None:
    """관리자 API 게이트. 계정 체계 없이 .env의 단일 키만 검증한다.

    `!=` 대신 `secrets.compare_digest`를 쓴다 — 이 키 하나가 LLM 과금 실행·분석
    삭제·스케줄 변경 권한 전부이고 시도 횟수 제한이 없어서, 첫 불일치 바이트에서
    빠져나가는 비교는 대입 공격에 앞자리부터 흘려준다.

    양쪽을 bytes로 인코딩해 넘긴다. compare_digest는 str을 받으면 ASCII만 허용해
    비ASCII 헤더 한 글자에 TypeError(=500)를 낸다 — 헤더는 latin-1로 디코드되므로
    공격자가 아니라 오타만으로도 닿는 경로다.
    """
    if not settings.admin_key or not secrets.compare_digest(
        x_admin_key.encode("utf-8"), settings.admin_key.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="관리자 키가 올바르지 않습니다.")
