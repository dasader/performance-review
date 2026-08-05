"""국가 코드 헬퍼 — 콤마 구분 목록의 파싱 규약을 한 곳에 둔다.

여섯 곳(admin 라우터 4 · public 라우터 1 · runner 1)이 같은 한 줄을
각자 갖고 있었고 이미 갈라져 있었다 — runner의 사본만 `.upper()`가 빠져 있어
스케줄 설정에 소문자 코드가 들어오면 그 국가로 큐잉된 분석이 격자·비교
경로(전부 대문자)와 영영 매칭되지 않는다. `_time.py`와 같은 이유의 모듈이다.
"""

from collections.abc import Iterable


def parse_countries(raw: str | Iterable[str] | None) -> list[str]:
    """콤마 구분 문자열(또는 문자열 목록)을 대문자 코드 목록으로. 순서는 보존한다.

    격자 열 순서가 스케줄 설정의 입력 순서를 따르므로 정렬하지 않는다 —
    정렬이 필요한 호출부는 `sorted(parse_countries(...))`로 감싼다.
    """
    items = raw.split(",") if isinstance(raw, str) else (raw or ())
    seen: list[str] = []
    for item in items:
        code = item.strip().upper()
        if code and code not in seen:
            seen.append(code)
    return seen


def invalid_countries(codes: Iterable[str]) -> list[str]:
    """두 글자 알파벳이 아닌 코드들. 형식을 막지 않으면 스케줄러가 존재하지 않는
    국가로 검색을 돌려 0건을 받는다(오류도 안 난다)."""
    return [c for c in codes if len(c) != 2 or not c.isalpha()]
