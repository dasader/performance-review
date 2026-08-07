import re
from datetime import datetime, timezone
from typing import Any, Sequence

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.clients._html import strip_html
from app.config import settings
from app.database import get_db
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import (
    CountryComparison,
    Field,
    FieldReport,
    Roadmap,
    RoadmapCheck,
    Subfield,
)
from app.models.paper import Paper, PaperExtraction
from app.services import comparison, mapper, stats
from app.services import visitors as visitors_service
from app.prompts import country_name
from app.services._countries import parse_countries
from app.services.runner import STEP_LABELS

router = APIRouter(prefix="/api", tags=["public"])

# 이 라우터의 목록·요약 집계(list_fields의 current_year_done, field_years,
# field_summary)는 한국어 보고서를 훑어보는 경로다 — 다른 국가는 국가 줄·비교
# 격자로 따로 들어간다. 국가 필터 없이 Analysis를 세면 같은 세부기술의 KR·US
# 행이 섞여 상태·건수가 뒤바뀐다(리뷰 지적: KR 링크인데 US의 상태·건수가 뜸,
# 6개 세부기술 × 2개국이 "12"로 세어짐). 리터럴을 여기저기 흩뿌리지 않도록
# 상수 하나로 고정한다.
_KOREA = "KR"

# 공개 응답에는 내부 예외 문자열을 싣지 않는다.
#
# runner·reducer·comparison은 실패를 `row.error = str(e)`로 남기는데(runner.py의
# 네 갈래, reducer._process_report, comparison), 그 예외가 항상 우리가 만든 문장인
# 것은 아니다 — Gemini SDK·psycopg2·httpx가 올리는 것이 그대로 실리면 내부 호스트명·
# 파일 경로·설정값이 익명 방문자에게 나간다. 화면 셋(Report·ComparisonPage·
# GeneratedReportSection)은 이미 자기 쪽에서 "분석이 실패했습니다" / "생성 실패:"를
# 붙이므로 뒤에 붙을 한 문장이면 충분하다.
#
# **원문은 관리자 화면이 그대로 받는다**(admin 대시보드 격자·분야 보고서 탭) — 원인을
# 봐야 하는 사람은 관리자이고, 그쪽은 인증 뒤에 있다. 운영에서 잃는 정보가 없다.
_PUBLIC_ERROR = "처리 중 오류가 발생했습니다. 관리자에게 문의해 주세요."


def _public_error(error: str | None) -> str | None:
    """실패 사실만 남기고 내부 문자열은 버린다. 없던 실패를 만들지 않도록 None은 None."""
    return _PUBLIC_ERROR if error else None


# 인용 형식 두 가지를 함께 잡는다.
#
# ① 괄호 — 한 단계 중첩까지 허용한다: 인용문 안에 "TrioN (3N0C)"처럼 논문 제목 자체가
#    괄호를 품는 경우, 바깥 인용 괄호 전체를 잡아야지 안쪽 괄호만 잡으면 안 된다(그러면
#    바깥 인용 전체가 미치환으로 남는다). `(?:[^()]|\([^()]*\))*` — 괄호가 아닌 문자이거나,
#    괄호가 없는 내용의 중첩 괄호 한 겹. 두 단계 이상 중첩은 다루지 않는다 — 실제 인용
#    데이터는 한 겹이면 충분하고, 그 이상은 재귀적 괄호 파서가 필요해 복잡도만 커진다.
# ② 백틱(코드 스팬) — LLM이 괄호 대신 이 형식으로 인용하는 경우가 실제로 있다.
#    실측(안전·신뢰 AI 2026): 서술부 인용 26건 중 23건이 백틱이고 괄호는 3건뿐이라,
#    괄호만 보던 매칭이 제목을 통째로 노출시켰다. 백틱 안에는 코드·용어도 들어오지만
#    치환은 "내용이 실제 논문 제목과 맞는가"로 판정하므로(_MIN_PARTIAL_TITLE_LEN 포함)
#    제목이 아닌 코드 스팬은 그대로 남는다.
_CITE_RE = re.compile(r"\(((?:[^()]|\([^()]*\))+)\)|`([^`\n]+)`")

# 치환 후 "*   [\[1\]](#ref-1)"처럼 인용만 남는 불릿 — LLM이 인용을 문단 안이 아니라
# 목록으로 나열하면 번호만 있는 항목이 줄줄이 쌓인다(실측 subfield 8 / 2026: 서술부
# 불릿 30줄). 연속된 그런 줄을 한 문단으로 접는다. 본문이 있는 불릿은 건드리지 않는다.
_FOOTNOTE_LINK = r"\[\\\[\d+\\\]\]\(#ref-\d+\)"
_FOOTNOTE_ONLY_BULLETS_RE = re.compile(
    rf"(?:^[ \t]*[-*][ \t]+(?:{_FOOTNOTE_LINK}[ \t]*)+$\n?)+", re.M
)

# 짧은 제목(정규화 키 기준 15자 미만)은 괄호 안 텍스트에 부분 문자열로 우연히 등장할
# 위험이 커서(예: 제목이 "AI"면 "(AI 기반 진단 시스템)"에도 걸림) 부분 매칭 대상에서
# 제외하고 완전 일치만 허용한다. 15는 실측 데이터(짧아도 단어 2~3개는 되는 실제 논문
# 제목들) 기준의 여유 있는 하한선이다 — 과학적으로 도출된 값은 아니다.
_MIN_PARTIAL_TITLE_LEN = 15

# LLM이 긴 제목을 "..."로 잘라 인용하는 경우 — 실측(차세대 메모리반도체 KR 2025 재실행
# 후): 서술부 인용 10건 중 8건이 잘린 형태라 각주가 36개에서 4개로 줄었다. 매칭은
# "DB 제목이 인용문에 포함되는가"를 보는데 잘린 인용은 반대(인용문이 제목의 앞부분)라
# 걸리지 않는다. 말줄임 표시를 떼고 접두사로 한 번 더 본다.
#
# 접두사 매칭은 부분 매칭보다 오탐 위험이 크다(제목 앞부분은 분야 상용구가 겹치기 쉽다
# — "Enhanced Performance of..."). 그래서 하한을 부분 매칭(15)보다 높게 잡는다.
_TRUNCATION_RE = re.compile(r"(?:\.\.\.|…)\s*$")
_MIN_TRUNCATED_PREFIX_LEN = 30


def _footnote_key(s: str) -> str:
    """각주 매칭용 비교 키: strip_html로 태그를 벗긴 뒤 공백을 전부 제거하고 소문자화한다.

    OpenAlex 원문은 태그 자리에 공백이 있을 때도("Hf <sub>0.5</sub> Zr" -> "Hf 0.5 Zr")
    없을 때도("MoS<sub>2</sub>" -> "MoS2") 있어 DB에 저장된 제목과 LLM이 실제로 인용한
    문자열 사이에 공백 개수가 어긋난다("Hf 0.5 Zr 0.5 O 2" vs "Hf0.5Zr0.5O2"). 공백을
    "하나로 접기"가 아니라 완전히 제거해야 이 둘이 같은 키로 만난다. 길이 판단
    (_MIN_PARTIAL_TITLE_LEN)도 이 키 기준으로 한다 — 실제 오탐 위험은 원문 글자 수가
    아니라 이 키(부분 문자열 검사에 실제로 쓰이는 문자열)의 길이에 달려 있다."""
    return "".join(strip_html(s).split()).lower()


def _apply_footnotes(report_md: str | None, papers: Sequence[Any]) -> tuple[str | None, list[dict]]:
    """report_md 안에서 괄호로 인용된 논문 제목을 각주 번호로 치환한다(읽기 시점 후처리,
    LLM 재호출 없음). LLM이 실행마다 인용 형식을 조금씩 다르게 쓰므로(연도 접두사, 공백 차이,
    구버전 보고서에 남은 HTML 태그 등) 매칭은 정규화 키("괄호 안 어딘가에 제목의 키가
    포함되는가")로 본다 — 괄호 전체와 정확히 같아야만 치환하던 이전 방식은 `([2025] 제목)`
    같은 형태를 전혀 못 잡았고, 단어를 `\\s+`로 이어 붙인 정규식 방식은 공백 개수 자체가
    달라지는 경우(OpenAlex 원문 태그 공백, 구버전 보고서의 남은 태그)를 못 잡았다. 다만 짧은
    제목은 부분 매칭 시 오탐 위험이 커서(_MIN_PARTIAL_TITLE_LEN 참고) 키 완전 일치만
    허용한다. 못 찾은 제목은 원문 그대로 둔다 — 잘못 치환하는 것보다 안전하다.

    제목은 긴 것부터(키 길이 기준) 시도한다. 한 제목이 다른 제목의 부분 문자열인 경우(예:
    "Graphene Growth Method"가 "Advanced Graphene Growth Method for Flexible Devices"에
    포함), 짧은 쪽을 먼저 보면 실제로는 긴 제목이 인용된 괄호가 짧은 제목의 논문으로 잘못
    귀속된다.

    각주는 `[\\[1\\]](#ref-1)` 형태의 마크다운 링크로 치환된다 — 대괄호를 이스케이프해
    렌더링 시 보이는 텍스트는 그대로 "[1]"이면서 "#ref-1"(참고문헌 항목)로 이동하는 링크가
    된다. 첫 인용 지점에 돌아올 앵커(id="cite-1")를 붙이는 책임은 프론트(Report.tsx)에
    있다 — 같은 논문이 여러 번 인용될 수 있어 "첫 인용 지점"을 여기(파이썬)에서 가리려면
    별도 상태 추적이 필요하지만, 프론트는 이미 각 인용 링크를 렌더링하는 시점에 있으므로
    처음 본 #ref-n을 추적하는 쪽이 더 단순하다(추가 마크업/원시 HTML도 필요 없음)."""
    if not report_md:
        return report_md, []

    # (paper, 정규화 키) 목록. 키 길이 내림차순 — 위 docstring의 부분 문자열 문제 방지.
    candidates: list[tuple[Any, str]] = sorted(
        ((p, _footnote_key(p.title)) for p in papers if p.title and p.title.strip()),
        key=lambda pc: len(pc[1]),
        reverse=True,
    )

    numbers: dict[int, int] = {}  # paper.id -> 각주 번호
    references: list[dict] = []

    def _lookup(text: str) -> Any | None:
        """인용 텍스트 하나에 대응하는 논문을 찾는다. 못 찾으면 None."""
        content_key = _footnote_key(text)
        truncated = bool(_TRUNCATION_RE.search(text.strip()))
        prefix_key = _footnote_key(_TRUNCATION_RE.sub("", text.strip()))
        for paper, key in candidates:
            if len(key) < _MIN_PARTIAL_TITLE_LEN:
                if content_key != key:
                    continue
            elif key not in content_key:
                # 잘린 인용은 방향이 반대다 — 인용문이 제목의 앞부분이다.
                if not (
                    truncated
                    and len(prefix_key) >= _MIN_TRUNCATED_PREFIX_LEN
                    and key.startswith(prefix_key)
                ):
                    continue
            return paper
        return None

    def _number(paper: Any) -> int:
        n = numbers.get(paper.id)
        if n is None:
            n = len(references) + 1
            numbers[paper.id] = n
            references.append({
                "n": n,
                "title": paper.title,
                "journal": paper.journal,
                "year": paper.year,
                "doi": paper.doi,
            })
        return n

    def repl(m: re.Match) -> str:
        # group(1)=괄호 인용, group(2)=백틱 인용. 둘 중 매칭된 쪽을 쓴다.
        content = m.group(1) if m.group(1) is not None else m.group(2)

        # 한 괄호에 ";"로 여러 논문을 나열한 인용 — 실측(차세대 메모리반도체 KR 2025):
        # 잘림 매칭을 고친 뒤 남은 미치환 8건 중 5건이 이 형태였다. 전체를 하나의 제목으로
        # 보면 어느 쪽과도 안 맞는다.
        #
        # 하나라도 못 찾으면 통째로 원문을 둔다. 부분 매칭만으로 괄호 전체를 치환하면
        # 매칭 안 된 쪽 텍스트가 조용히 사라진다(이 방어가 없을 때 실제로 그랬다).
        if ";" in content:
            parts = [x.strip() for x in content.split(";") if x.strip()]
            found = [_lookup(x) for x in parts]
            if len(parts) > 1 and all(f is not None for f in found):
                return "".join(f"[\\[{_number(f)}\\]](#ref-{_number(f)})" for f in found)
            return m.group(0)

        matched = _lookup(content)
        if matched is None:
            return m.group(0)
        n = _number(matched)
        return f"[\\[{n}\\]](#ref-{n})"

    substituted = _CITE_RE.sub(repl, report_md)

    def collapse(m: re.Match) -> str:
        """인용만 남은 불릿 묶음을 각주가 이어진 한 문단으로 접는다."""
        links = re.findall(_FOOTNOTE_LINK, m.group(0))
        return "".join(links) + "\n"

    return _FOOTNOTE_ONLY_BULLETS_RE.sub(collapse, substituted), references


# 종합 보고서와 세부 보고서를 한 번에 치환하려고 이어 붙일 때 쓰는 구분자.
# 본문에 나타날 수 없어야 해서 널 문자를 쓴다 — 치환 후 다시 이 문자열로 쪼갠다.
_SECTION_SEP = "\x00SECTION\x00"


def _footnoted_report(
    db: Session, analysis: Analysis
) -> tuple[str | None, list[dict], list[dict]]:
    """analysis의 report_md와 세부 보고서에 각주 치환을 적용한다.

    반환은 (치환된 종합 보고서, references, 세부 보고서 목록).

    종합 보고서와 세부 보고서를 **한 번에** 치환해 번호 체계를 공유한다 — 따로 매기면
    세부 보고서를 펼쳤을 때 [12]가 종합 보고서의 [12]와 다른 논문을 가리킨다.

    report_md 원문은 "(논문 제목)" 형태로 저장돼 있고, 각주 [n] 치환은 조회 시점에
    한다 — 세부기술 보고서 화면과 분야 종합보고서 부록(세부기술 첨부)이 이걸 공유한다.
    빼먹으면 논문 제목이 full name 그대로 노출된다."""
    # 각주는 id/title/journal/year/doi만 쓴다. 전체 ORM 행을 실으면 abstract(논문당
    # 1~2KB)까지 딸려와, 703건짜리 보고서 한 번 여는 데 1MB 가까이 헛읽는다.
    papers = db.query(
        Paper.id, Paper.title, Paper.journal, Paper.year, Paper.doi
    ).join(AnalysisPaper, AnalysisPaper.paper_id == Paper.id).filter(
        AnalysisPaper.analysis_id == analysis.id
    ).all()

    sections = analysis.sections_json or []
    if not sections:
        report_md, references = _apply_footnotes(analysis.report_md, papers)
        return report_md, references, []

    # 구분자 앞뒤에 빈 줄을 둬 마크다운 블록이 서로 섞이지 않게 한다. 다시 쪼갤 때는
    # 개행을 뺀 구분자만 쓴다 — _apply_footnotes의 불릿 접기가 줄 끝 개행을 소비할 수
    # 있어 앞뒤 개행 수가 그대로 남는다고 가정하면 안 된다.
    combined = f"\n\n{_SECTION_SEP}\n\n".join(
        [analysis.report_md or ""] + [s.get("body") or "" for s in sections]
    )
    substituted, references = _apply_footnotes(combined, papers)
    parts = (substituted or "").split(_SECTION_SEP)
    return (
        parts[0].strip() if analysis.report_md is not None else None,
        references,
        [
            {"name": s.get("name") or "", "body_md": body.strip()}
            for s, body in zip(sections, parts[1:])
        ],
    )


def _serialize(db: Session, analysis: Analysis) -> dict:
    subfield = db.get(Subfield, analysis.subfield_id)
    field = db.get(Field, subfield.field_id)

    report_md, references, sections = _footnoted_report(db, analysis)

    # 보고서 화면의 이동(목록으로 · 이전/다음 연도)에 필요한 최소 정보. 연도 목록은
    # 이 세부기술에 analyses 행이 있는 연도 전부다 — 미완료 연도로 가면 그 화면이
    # 진행 상태를 보여주므로 status로 거르지 않는다.
    years = [
        y for (y,) in db.query(Analysis.year)
        # 같은 국가의 연도만 — 다른 국가 연도가 섞이면 이동 링크가 404로 간다.
        .filter(Analysis.subfield_id == subfield.id, Analysis.country == analysis.country)
        .order_by(Analysis.year)
        .all()
    ]

    return {
        "id": analysis.id,
        "field_id": field.id,
        "field_name": field.name,
        "subfield_id": subfield.id,
        "subfield_name": subfield.name,
        "years": years,
        "year": analysis.year,
        "status": analysis.status,
        "status_label": STEP_LABELS.get(analysis.status, analysis.status),
        "report_md": report_md,
        "references": references,
        "sections": sections,
        "country": analysis.country,
        "country_name": country_name(analysis.country),
        "stats": analysis.stats_json,
        "searched_count": analysis.searched_count,
        "analyzed_count": analysis.analyzed_count,
        "snapshot_at": analysis.snapshot_at.isoformat() if analysis.snapshot_at else None,
        "error": _public_error(analysis.error),
    }


@router.get("/fields")
def list_fields(db: Session = Depends(get_db)):
    fields = db.query(Field).order_by(Field.order_no).all()
    # 분야마다 따로 조회하면 랜딩 페이지 한 번에 11번 질의가 나간다 — 한 번에 읽어 묶는다.
    subfields_by_field: dict[int, list] = {}
    for s in db.query(Subfield).all():
        subfields_by_field.setdefault(s.field_id, []).append(s)

    # 랜딩 화면의 진행 파이("세부기술 4개 중 3개 분석됨")용. 여기서도 분야마다 따로
    # 세면 11번 질의가 되므로 당해연도 done 건수를 한 번에 읽어 분야별로 묶는다.
    # 대상 연도는 서버의 "올해"다 — 사용자가 고르는 값이 아니라 "지금 기준 최신
    # 연도의 진행 상황"을 보여주는 것이 이 화면의 목적이다.
    current_year = datetime.now(timezone.utc).year
    done_by_field = dict(
        db.query(Subfield.field_id, func.count(Analysis.id))
        .join(Analysis, Analysis.subfield_id == Subfield.id)
        .filter(
            Analysis.year == current_year,
            Analysis.status == "done",
            Analysis.report_md.isnot(None),
            # 파이의 분모가 활성 세부기술 수이므로 분자도 같은 모집단이어야 한다 —
            # 분석을 마친 세부기술을 나중에 비활성화하면 done이 분모를 넘어선다.
            Subfield.active.is_(True),
            # 국가 필터 없으면 US 분석까지 세어져 done이 분모(활성 세부기술 수)를
            # 넘어설 수 있다(리뷰 지적).
            Analysis.country == _KOREA,
        )
        .group_by(Subfield.field_id)
        .all()
    )

    return [
        {
            "id": f.id,
            "name": f.name,
            "slug": f.slug,
            "subfields": [
                {"id": s.id, "name": s.name, "active": s.active}
                for s in subfields_by_field.get(f.id, [])
            ],
            "current_year": current_year,
            "current_year_done": done_by_field.get(f.id, 0),
        }
        for f in fields
    ]


@router.get("/fields/{field_id}/years")
def field_years(field_id: int, db: Session = Depends(get_db)):
    """이 분야에서 보고서가 존재하는 연도 목록."""
    # 연도별로 세기만 하므로 두 컬럼이면 충분하다 — 전체 행을 실으면 report_md(건당
    # 12KB 규모)와 stats_json까지 읽어온다.
    rows = db.query(Analysis.year, Analysis.status).filter(
        Analysis.subfield_id.in_(
            db.query(Subfield.id).filter(Subfield.field_id == field_id)
        ),
        # 국가 필터 없으면 세부기술마다 국가 수만큼 중복 집계된다(리뷰 지적:
        # 6개 세부기술 × 2개국이 "(12/24)"로 뜸).
        Analysis.country == _KOREA,
    ).all()

    by_year: dict[int, dict] = {}
    for row in rows:
        entry = by_year.setdefault(row.year, {"year": row.year, "subfield_count": 0, "done_count": 0})
        entry["subfield_count"] += 1
        if row.status == "done":
            entry["done_count"] += 1
    return sorted(by_year.values(), key=lambda e: e["year"], reverse=True)


@router.get("/fields/{field_id}/summary")
def field_summary(field_id: int, year: int, db: Session = Depends(get_db)):
    """세부기술별 논문 분포 — 대분류 레벨에서 세부기술들을 비교할 때만 의미가 있다.

    analyses 행이 없는 세부기술도 analysis_id=null / status="미실행"으로 포함한다.
    빠뜨리면 분포가 왜곡된다.
    """
    field = db.get(Field, field_id)
    if not field:
        raise HTTPException(status_code=404, detail="분야를 찾을 수 없습니다.")

    subfields = db.query(Subfield).filter(Subfield.field_id == field_id).order_by(Subfield.name).all()
    # 여기서 읽는 건 다섯 컬럼뿐이다 — 전체 행을 실으면 report_md(건당 12KB 규모)와
    # stats_json까지 딸려온다(dashboard·field_years와 같은 이유).
    analyses_by_subfield = {
        a.subfield_id: a
        for a in db.query(
            Analysis.id, Analysis.subfield_id, Analysis.status,
            Analysis.searched_count, Analysis.analyzed_count,
        ).filter(
            Analysis.subfield_id.in_([s.id for s in subfields]),
            Analysis.year == year,
            # 국가 필터 없으면 {subfield_id: analysis} 맵이 국가 무관 "아무 분석"이
            # 되어, 행의 상태·검색/분석 건수가 US 것인데 링크는 KR 보고서로 여는
            # 어긋남이 생긴다(리뷰 지적). 이 화면은 한국어 보고서 목록이다.
            Analysis.country == _KOREA,
        )
    }
    # 국가 목록은 세부기술마다 질의하면 55번 나간다 — 한 번에 모아 읽어 붙인다.
    countries_by_subfield: dict[int, list[str]] = {}
    for row in db.query(Analysis.subfield_id, Analysis.country).filter(
        Analysis.subfield_id.in_([s.id for s in subfields]),
        Analysis.year == year,
        Analysis.status == "done",
    ):
        countries_by_subfield.setdefault(row.subfield_id, []).append(row.country)

    rows = []
    total_searched = total_analyzed = 0
    for s in subfields:
        a = analyses_by_subfield.get(s.id)
        searched = a.searched_count if a else 0
        analyzed = a.analyzed_count if a else 0
        total_searched += searched
        total_analyzed += analyzed
        rows.append({
            "subfield_id": s.id,
            "subfield_name": s.name,
            "analysis_id": a.id if a else None,
            "status": a.status if a else "미실행",
            "status_label": STEP_LABELS.get(a.status, a.status) if a else "미실행",
            "searched_count": searched,
            "analyzed_count": analyzed,
            "countries": sorted(countries_by_subfield.get(s.id, [])),
        })

    return {
        "field_name": field.name,
        "year": year,
        "subfields": rows,
        "total_searched": total_searched,
        "total_analyzed": total_analyzed,
    }


def _done_report_count(db: Session, field_id: int, year: int) -> int:
    """지금 완성돼 있는 세부기술 보고서 수. 생성 시점의 source_count와 비교해
    "재생성이 필요한가"(stale)를 판정한다. report_md는 건당 12KB라 count로만 읽는다."""
    return (
        db.query(Analysis.id)
        .join(Subfield, Analysis.subfield_id == Subfield.id)
        .filter(
            Subfield.field_id == field_id,
            Analysis.year == year,
            Analysis.status == "done",
            Analysis.report_md.isnot(None),
        )
        .count()
    )


@router.get("/fields/{field_id}/report")
def field_report(field_id: int, year: int, db: Session = Depends(get_db)):
    """대분류 보고서 조회. 생성은 관리자만 할 수 있고(POST /api/admin/fields/{id}/report)
    여기서는 캐시된 결과만 읽는다."""
    row = (
        db.query(FieldReport)
        .filter(FieldReport.field_id == field_id, FieldReport.year == year)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="분야 보고서가 아직 생성되지 않았습니다.")

    current = _done_report_count(db, field_id, year)
    return {
        "field_id": row.field_id,
        "year": row.year,
        # pending/failed도 그대로 내려준다 — 처음 생성 중이면 report_md는 빈 문자열,
        # 재생성 중이면 이전 본문이 담겨 있어 화면이 옛 보고서를 보여주며 폴링한다.
        "status": row.status,
        "error": _public_error(row.error),
        "report_md": row.report_md,
        "source_count": row.source_count,
        "current_count": current,
        "stale": current != row.source_count,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }


@router.get("/fields/{field_id}/roadmap-check")
def roadmap_check(field_id: int, year: int, db: Session = Depends(get_db)):
    """로드맵 이행 점검 보고서 조회. 캐시된 결과만 읽는다.

    로드맵 원문 자체는 내려주지 않는다 — 비공개 판본일 수 있고, 화면에 필요한 건
    점검 결과이지 원문이 아니다.
    """
    row = (
        db.query(RoadmapCheck)
        .filter(RoadmapCheck.field_id == field_id, RoadmapCheck.year == year)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="로드맵 점검 보고서가 아직 생성되지 않았습니다.")

    current = _done_report_count(db, field_id, year)
    roadmap = db.query(Roadmap).filter(Roadmap.field_id == field_id).one_or_none()
    return {
        "field_id": row.field_id,
        "year": row.year,
        "status": row.status,
        "error": _public_error(row.error),
        "report_md": row.report_md,
        "source_count": row.source_count,
        "current_count": current,
        "goal_count": row.goal_count,
        "checked_count": row.checked_count,
        # 전수 점검이 깨진 채로 저장된 보고서 — 빠진 목표가 있다는 뜻이라
        # "빠짐없이 점검했다"로 읽히면 안 된다.
        "incomplete": row.checked_count != row.goal_count,
        "roadmap_version": row.roadmap_version,
        # 세부기술 보고서가 늘었거나 로드맵 판본이 바뀌면 재생성 대상이다.
        "stale": current != row.source_count
        or (roadmap is not None and roadmap.version_label != row.roadmap_version),
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }


@router.get("/fields/{field_id}/subfield-reports")
def subfield_reports(field_id: int, year: int, db: Session = Depends(get_db)):
    """분야 종합 보고서 전용 페이지의 "세부기술 보고서 포함" 토글용 — 이 분야·연도에서
    완성된 세부기술 보고서를 이어붙이기 좋게 목록으로 내려준다.

    세부기술 보고서 화면(get_by_subfield_year)과 똑같이 각주 치환을 적용한다 —
    빼면 부록에 논문 제목이 full name 그대로 노출된다(_footnoted_report). 참고문헌도
    함께 내려 화면이 [n] 각주 아래 목록을 붙일 수 있게 한다.
    """
    # 세부기술마다 분석을 따로 조회하면 분야당 55번 나간다 — 조인 한 번으로 읽는다.
    # 국가는 다른 공개 집계와 같이 KR로 못 박는다. 안 걸면 세부기술마다 국가 수만큼
    # 행이 나와 report_md·stats_json을 통째로 읽고 하나만 남기고 버리게 되고,
    # 게다가 어느 국가가 남는지가 정해져 있지 않다.
    rows = (
        db.query(Subfield.name, Analysis)
        .join(Analysis, Analysis.subfield_id == Subfield.id)
        .filter(
            Subfield.field_id == field_id,
            Analysis.year == year,
            Analysis.status == "done",
            Analysis.country == _KOREA,
            Analysis.report_md.isnot(None),
            Analysis.report_md != "",
        )
        .order_by(Subfield.name)
        .all()
    )
    reports = []
    for name, analysis in rows:
        # 부록은 세부 보고서를 붙이지 않는다 — 이미 세부기술 보고서 본문을
        # 싣고 있어 유형별 상세까지 넣으면 과하다.
        md, references, _ = _footnoted_report(db, analysis)
        reports.append({"name": name, "report_md": md, "references": references})
    return {"field_id": field_id, "year": year, "reports": reports}


@router.get("/site-info")
def site_info(request: Request):
    """푸터에 표시할 도메인·버전. 버전은 프론트 package.json이 단일 출처이므로
    프론트는 이 값 대신 빌드 타임에 주입된 자기 자신의 버전을 쓴다 — 여기서는
    FastAPI 앱에 이미 있는 버전 속성을 그대로 노출한다."""
    return {"domain": settings.site_domain, "version": request.app.version}


@router.get("/visitors")
def get_visitors(db: Session = Depends(get_db)):
    return visitors_service.visitor_stats(db)


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int, db: Session = Depends(get_db)):
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="분석 결과를 찾을 수 없습니다.")
    return _serialize(db, analysis)


@router.get("/subfields/{subfield_id}/analyses/{year}")
def get_by_subfield_year(
    subfield_id: int, year: int, country: str = "KR", db: Session = Depends(get_db)
):
    analysis = db.query(Analysis).filter(
        Analysis.subfield_id == subfield_id,
        Analysis.year == year,
        Analysis.country == country,
    ).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="분석 결과를 찾을 수 없습니다.")
    return _serialize(db, analysis)


@router.get("/subfields/{subfield_id}/comparison")
def get_comparison(
    subfield_id: int, year: int, countries: str, db: Session = Depends(get_db)
):
    """비교 보고서 조회 — 캐시만 읽는다(생성은 관리자만).

    pending/failed도 그대로 내려준다. 화면이 status로 폴링·경고를 판단한다
    (분야 보고서와 같은 규약).
    """
    wanted = sorted(parse_countries(countries))
    key = ",".join(wanted)
    row = (
        db.query(CountryComparison)
        .filter(
            CountryComparison.subfield_id == subfield_id,
            CountryComparison.year == year,
            CountryComparison.countries == key,
        )
        .one_or_none()
    )

    subfield = db.get(Subfield, subfield_id)
    if row is not None:
        codes = row.countries.split(",")
        body, sections = row.report_md, row.sections_json
    else:
        found = _pair_inside_multi(db, subfield_id, year, wanted)
        if found is None:
            raise HTTPException(status_code=404, detail="비교 보고서가 아직 생성되지 않았습니다.")
        row, codes, body, sections = *found, []

    return {
        "subfield_id": row.subfield_id,
        "subfield_name": subfield.name if subfield else None,
        "year": row.year,
        "countries": codes,
        "country_names": [country_name(c) for c in codes],
        "status": row.status,
        "error": _public_error(row.error),
        "report_md": body,
        "source_count": len(codes),
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "sections": sections,
    }


def _pair_inside_multi(
    db: Session, subfield_id: int, year: int, wanted: list[str]
) -> tuple[CountryComparison, list[str], str] | None:
    """1:1 비교 요청을 이미 만들어진 다국 비교 안의 쌍별 섹션으로 넘긴다.

    3개국 이상 비교는 쌍별 1:1을 각각 독립된 LLM 콜로 만들어 sections_json에 담고
    그것을 종합한다(process_comparison). 그 섹션 본문은 2개국 전용 비교의 report_md와
    **같은 방식으로 만들어진 같은 값**이다 — 두 나라 통계만으로 대조표를 만들고 그
    둘만 넣어 생성한다. 그래서 1:1을 따로 생성하는 것은 이미 있는 것을 돈 주고 다시
    만드는 일이고, 대신 여기서 찾아 준다.

    쌍이 기준국(base_country)을 포함할 때만 성립한다 — "중국 vs 일본"처럼 기준국이
    빠진 쌍은 애초에 생성되지 않았으므로 폴백할 대상이 없다.
    """
    if len(wanted) != 2:
        return None
    for row in db.query(CountryComparison).filter(
        CountryComparison.subfield_id == subfield_id,
        CountryComparison.year == year,
        CountryComparison.status == "done",
    ):
        codes = row.countries.split(",")
        if len(codes) <= 2 or not set(wanted) <= set(codes):
            continue
        base = comparison.base_country(codes)
        if base not in wanted:
            continue
        other = next(c for c in wanted if c != base)
        name = f"{country_name(base)} vs {country_name(other)}"
        for section in row.sections_json or []:
            if section.get("name") == name:
                return row, [base, other], section.get("body") or ""
    return None


@router.get("/subfields/{subfield_id}/availability")
def subfield_availability(subfield_id: int, year: int, db: Session = Depends(get_db)):
    """이 세부기술·연도에 완성된 국가 분석과 비교 보고서 목록.

    화면의 국가 줄이 이것만 보고 링크를 만든다. **미보유는 아예 내려주지 않는다** —
    공개 화면 방문자에게 "아직 안 돌렸다"는 운영 사정을 보일 이유가 없다(그 정보가
    필요한 사람은 관리자이고, 관리자 격자가 전부 보여준다).
    """
    countries = sorted(
        a.country
        for a in db.query(Analysis.country).filter(
            Analysis.subfield_id == subfield_id,
            Analysis.year == year,
            Analysis.status == "done",
        )
    )
    # 이름표는 내려보내지 않는다 — CountryBar가 기준국(한국)을 빼고 자체 규칙으로
    # 만들기 때문에 서버가 만든 문자열은 화면에 한 번도 쓰이지 않았다.
    seen: list[list[str]] = []
    for c in (
        db.query(CountryComparison)
        .filter(
            CountryComparison.subfield_id == subfield_id,
            CountryComparison.year == year,
            CountryComparison.status == "done",
        )
        .order_by(CountryComparison.countries)
    ):
        codes = c.countries.split(",")
        if codes not in seen:
            seen.append(codes)
        # 다국 비교 안의 쌍별 1:1도 링크로 낸다. 본문은 이미 sections_json에 있고
        # 조회는 _pair_inside_multi가 넘겨주므로 별도 생성 없이 읽을 수 있다 —
        # 여기서 내주지 않으면 그 폴백으로 갈 길이 화면에 없다.
        if len(codes) > 2:
            base = comparison.base_country(codes)
            for other in codes:
                pair = sorted((base, other))
                if other != base and pair not in seen:
                    seen.append(pair)
    return {"countries": countries, "comparisons": [{"countries": c} for c in seen]}


@router.get("/analyses/{analysis_id}/metrics")
def metric_drilldown(analysis_id: int, name: str, unit: str = "", db: Session = Depends(get_db)):
    """지표 표의 한 행("전력변환효율 447편") 뒤에 있는 논문 목록.

    저장하지 않고 조회 시점에 계산한다 — stats_json에 논문 키를 넣으면 PCE 하나만
    447개 키라 blob이 MB 단위로 부풀고 재계산 때마다 커진다.

    묶음은 stats._metric_key/_metric_value를 그대로 재사용한다. 다른 정규화를 쓰면
    "표는 447편인데 목록은 12편"이 되어 화면이 스스로를 반박한다.

    이상값을 기계적으로 거를 수 없기 때문에 필요한 화면이다 — "전력변환효율"이라는
    같은 이름 아래 태양전지 PCE와 전력회로 변환효율이 섞이고, 값만 보고는 "오류"와
    "다른 대상"을 구분할 수 없다(§10-2). 지우는 대신 확인 가능하게 만든다.
    """
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="분석을 찾을 수 없습니다.")

    want = (stats._metric_key(name), unit.strip())

    # 이 분석에 실제로 링크된 논문만 본다 — paper_extractions는 세부기술 단위 캐시라
    # 다른 연도 논문까지 들어 있다.
    # 각주(_footnoted_report)와 같은 이유로 컬럼만 고른다 — 전체 ORM 행을 실으면
    # abstract(논문당 1~2KB)까지 딸려와 5,000건짜리 분석에서 수 MB를 헛읽는다.
    titles = {
        p.paper_key: p
        for p in db.query(Paper.paper_key, Paper.title, Paper.journal, Paper.year, Paper.doi)
        .join(AnalysisPaper, AnalysisPaper.paper_id == Paper.id)
        .filter(AnalysisPaper.analysis_id == analysis.id)
    }
    extractions = (
        db.query(PaperExtraction)
        .filter(
            PaperExtraction.subfield_id == analysis.subfield_id,
            PaperExtraction.model_ver == mapper.model_ver(),
            PaperExtraction.paper_key.in_(titles.keys()) if titles else False,
        )
        .all()
    )

    rows = []
    for extraction in extractions:
        paper = titles.get(extraction.paper_key)
        for metric in extraction.metrics_json or []:
            if not isinstance(metric, dict):
                continue
            metric_name = (metric.get("name") or "").strip()
            if (stats._metric_key(metric_name), (metric.get("unit") or "").strip()) != want:
                continue
            value = stats._metric_value(metric.get("value"))
            if value is None:
                continue
            rows.append({
                "value": value,
                "raw": metric.get("value"),
                "target": metric.get("target"),
                "label": metric_name,
                "title": paper.title if paper else None,
                "journal": paper.journal if paper else None,
                "year": paper.year if paper else None,
                "doi": paper.doi if paper else None,
            })

    # 값 내림차순 — 이상값 확인이 이 화면의 목적이라 큰 값이 위로 온다.
    rows.sort(key=lambda r: r["value"], reverse=True)
    return {"name": name, "unit": unit, "count": len(rows), "rows": rows}
