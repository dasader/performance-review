import sys

from app.config import Settings


def test_import_succeeds_without_gemini_api_key(monkeypatch):
    """빈 GEMINI_API_KEY로도 import(=컨테이너 기동)가 실패하면 안 된다.
    클라이언트는 실제 호출 시점까지 지연 생성돼야 한다.

    google-genai SDK는 api_key=""가 falsy면 GEMINI_API_KEY/GOOGLE_API_KEY 환경변수로
    폴백하므로(conftest가 테스트 세션 전체에 test-key를 심어둔다), 그 폴백까지 막아야
    실제로 "빈 키" 상황을 재현한다.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.settings",
        Settings(gemini_api_key="", openalex_api_key="k", admin_key="a",
                  database_url="postgresql://x/y"),
    )
    for mod in ("app.clients.gemini_batch", "app.clients.gemini_sync"):
        sys.modules.pop(mod, None)

    import app.clients.gemini_batch  # noqa: F401
    import app.clients.gemini_sync  # noqa: F401
