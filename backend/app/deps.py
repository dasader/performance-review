from fastapi import Header, HTTPException

from app.config import settings


def require_admin(x_admin_key: str = Header(default="")) -> None:
    """관리자 API 게이트. 계정 체계 없이 .env의 단일 키만 검증한다."""
    if not settings.admin_key or x_admin_key != settings.admin_key:
        raise HTTPException(status_code=401, detail="관리자 키가 올바르지 않습니다.")
