"""국가 비교 보고서 — 모델·대조표·큐잉·처리."""

from datetime import datetime

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Analysis, CountryComparison, Field, Subfield
from app.services import comparison


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def test_country_comparison_roundtrip():
    """국가 목록은 콤마 문자열로 저장되고, 생성 전 기본값은 FieldReport와 같다."""
    db = _session()
    db.add(
        CountryComparison(
            subfield_id=1,
            year=2026,
            countries="CN,KR,US",
            generated_at=datetime(2026, 8, 3),
        )
    )
    db.commit()

    saved = db.query(CountryComparison).one()
    assert saved.countries == "CN,KR,US"
    # 생성 전에는 빈 본문 — 재생성 중에도 옛 본문을 남기기 위해 nullable이 아니다
    assert saved.status == "done"
    assert saved.report_md == ""
    assert saved.source_count == 0


def _stats(**over):
    base = {
        "searched_count": 300,
        "analyzed_count": 245,
        "population_total": 300,
        "sampled": False,
        "no_abstract_count": 55,
        "attribution": {"단독": 200, "주도": 30, "참여": 10, "주도 미상": 5},
        "citations": {"median": 2, "p90": 12, "total": 900},
        "by_achievement_type": {"신소자": 100, "공정": 45},
        "intl_collab_ratio": 0.18,
    }
    base.update(over)
    return base


def test_comparison_table_has_a_column_per_country():
    rows = [
        ("KR", _stats()),
        ("CN", _stats(searched_count=820, analyzed_count=731,
                      population_total=821, sampled=True)),
    ]
    table = comparison.build_comparison_table(rows)

    assert "한국" in table and "중국" in table
    for label in ("모집단", "수집", "표본율", "분석", "단독", "주도", "참여"):
        assert label in table
    # 숫자가 그대로 실린다 — LLM이 계산하지 않는다
    assert "731" in table and "245" in table


def test_comparison_table_marks_sampled_country():
    """표본율 행이 없으면 프롬프트가 '인용수 비교 금지'를 판단할 근거를 잃는다."""
    rows = [
        ("KR", _stats(population_total=300, searched_count=300, sampled=False)),
        ("US", _stats(population_total=7785, searched_count=5000, sampled=True)),
    ]
    table = comparison.build_comparison_table(rows)

    assert "100%" in table   # KR
    assert "64%" in table    # US: 5000/7785


def test_comparison_table_includes_achievement_types():
    """성과유형은 국가마다 키가 달라 합집합을 만들고 없는 곳은 0으로 채운다 —
    빠뜨리면 '그 국가엔 그 유형이 없다'와 '집계에서 누락됐다'가 구별되지 않는다."""
    rows = [
        ("KR", _stats(by_achievement_type={"신소자": 100, "공정": 45})),
        ("CN", _stats(by_achievement_type={"신소자": 96, "아키텍처": 300})),
    ]
    table = comparison.build_comparison_table(rows)

    assert "아키텍처" in table and "공정" in table
    lines = [l for l in table.splitlines() if l.startswith("| 아키텍처")]
    assert len(lines) == 1
    assert "| 0 |" in lines[0]   # KR엔 없으므로 0


def test_comparison_table_labels_the_base_of_each_group():
    """귀속과 성과유형은 모수가 다르다 — 실측(차세대 메모리반도체 2025):
    귀속 합계는 수집(820)과 같고 성과유형 합계는 분석(731)과 같다.
    국가 정보는 초록이 없어도 메타데이터에 있지만 성과유형은 추출 결과라서다.

    표에 기준이 안 드러나면 '단독+주도+참여가 분석 건수와 안 맞는다'로 읽혀
    비교 보고서가 숫자를 불신하거나 잘못 합산한다."""
    rows = [("KR", _stats()), ("CN", _stats())]
    table = comparison.build_comparison_table(rows)

    assert "귀속" in table and "수집 기준" in table
    assert "성과유형" in table and "분석 기준" in table


def _seed(db, countries=("KR",), *, year=2026, report="# 보고서", stats=None):
    """세부기술 1개 + 지정 국가의 done 분석을 심는다.

    country를 반드시 명시하는 이유: SQLAlchemy의 default=는 INSERT 시점에만 적용돼
    직접 만든 객체에는 안 들어간다(4단계에서 헤더가 'None'으로 나온 원인).
    """
    import json as _json

    f = Field(name="반도체·디스플레이", slug="semi", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="차세대 메모리반도체", query="memory", query_kci=None)
    db.add(sf)
    db.flush()
    for c in countries:
        db.add(
            Analysis(
                subfield_id=sf.id,
                year=year,
                country=c,
                status="done",
                query_hash="h",
                report_md=report,
                stats_json=_json.dumps(stats or _stats()),
            )
        )
    db.commit()
    return sf.id


def test_collect_requires_every_requested_country():
    """요청한 국가 중 하나라도 done 분석이 없으면 ValueError —
    일부만으로 만들면 '그 국가는 성과가 없다'로 오독된다."""
    db = _session()
    sid = _seed(db, ("KR",))

    with pytest.raises(ValueError, match="US"):
        comparison.collect_country_analyses(db, sid, 2026, ["KR", "US"])


def test_collect_returns_requested_order():
    db = _session()
    sid = _seed(db, ("KR", "US"))

    pairs = comparison.collect_country_analyses(db, sid, 2026, ["US", "KR"])
    assert [c for c, _ in pairs] == ["US", "KR"]


def test_collect_skips_empty_report():
    """본문이 빈 분석(논문 0건)은 없는 것으로 친다 — 합성에 넣어봐야
    모델이 근거 없이 채워 넣을 여지만 준다(rollup_field와 같은 이유)."""
    db = _session()
    sid = _seed(db, ("KR", "US"))
    db.query(Analysis).filter(Analysis.country == "US").one().report_md = ""
    db.commit()

    with pytest.raises(ValueError, match="US"):
        comparison.collect_country_analyses(db, sid, 2026, ["KR", "US"])


def test_compare_instruction_forbids_the_known_traps():
    """비교 보고서가 저지르기 쉬운 오독을 프롬프트가 직접 금지해야 한다.
    각 항목은 실측으로 확인된 함정이라 문구를 약화시키면 안 된다."""
    from app.prompts import COMPARE_INSTRUCTION

    # 길이 = 압축률 차이. 실측(2026-08-03): 3단 reduce에 들어간 12건의 종합 보고서가
    # 분석 501건이든 1,850건이든 약 5,000자에서 포화하고, 단일 reduce로 끝난 245건짜리는
    # 10,549자다. 금지하지 않으면 "논문 많은 쪽이 빈약하다"로 정반대 결론이 난다.
    assert "길이" in COMPARE_INSTRUCTION
    # 표본율이 다른 국가끼리 인용수·논문수 직접 비교 금지
    assert "표본율" in COMPARE_INSTRUCTION
    # 참여 기준 중복 계상 — 국가별 합계는 총합과 다르다
    assert "중복" in COMPARE_INSTRUCTION
    # 순위·점수 생성 금지
    assert "순위" in COMPARE_INSTRUCTION
    # 한계 절 강제
    assert "한계" in COMPARE_INSTRUCTION


def test_compare_instruction_forbids_recomputing_the_table():
    """대조표는 코드가 만든다. 모델이 다시 계산하면 틀린다."""
    from app.prompts import COMPARE_INSTRUCTION

    assert "계산" in COMPARE_INSTRUCTION


def test_enqueue_normalizes_country_order():
    """국가 순서가 달라도 같은 행을 재사용한다 — 안 그러면 같은 비교가
    순서만 바꿔 여러 행으로 쌓인다."""
    db = _session()
    sid = _seed(db, ("KR", "US"))

    a = comparison.enqueue_comparison(db, sid, 2026, ["US", "KR"])
    b = comparison.enqueue_comparison(db, sid, 2026, ["KR", "US"])
    assert a.id == b.id
    assert a.countries == "KR,US"


def test_enqueue_rejects_single_country():
    db = _session()
    sid = _seed(db, ("KR",))

    with pytest.raises(ValueError, match="2개"):
        comparison.enqueue_comparison(db, sid, 2026, ["KR"])


def test_enqueue_rejects_unknown_subfield():
    db = _session()
    _seed(db, ("KR", "US"))

    with pytest.raises(LookupError):
        comparison.enqueue_comparison(db, 999, 2026, ["KR", "US"])


def test_enqueue_keeps_old_body_on_regenerate():
    """재생성 큐잉은 status만 pending으로 되돌리고 본문은 남긴다 —
    처리 완료 전까지 이전 보고서를 계속 보여주기 위해서다(FieldReport와 같음)."""
    db = _session()
    sid = _seed(db, ("KR", "US"))

    row = comparison.enqueue_comparison(db, sid, 2026, ["KR", "US"])
    row.report_md = "# 이전 보고서"
    row.status = "done"
    db.commit()

    again = comparison.enqueue_comparison(db, sid, 2026, ["KR", "US"])
    assert again.status == "pending"
    assert again.report_md == "# 이전 보고서"


async def test_process_sends_table_and_bodies_only(monkeypatch):
    """LLM 입력에 대조표와 각국 종합 보고서가 들어가고, sections_json(세부 보고서)은
    들어가지 않는다.

    세부까지 넣으면 5개국에서 약 725KB(약 18만 토큰)가 되고, 2단계에서 확인한 이중
    압축을 비교 단계에서 반복한다(실측: CN 2025 세부 144,730자 vs 종합 4,813자)."""
    from app.services import comparison as comp

    db = _session()
    sid = _seed(db, ("KR", "US"))
    # 세부 보고서가 있어도 입력에 새어들면 안 된다
    a = db.query(Analysis).filter(Analysis.country == "KR").one()
    a.sections_json = '[{"name": "성과유형 A", "body": "세부내용-누출감지용"}]'
    db.commit()

    captured = {}

    async def fake_generate(system, user, *, thinking=None, **kw):
        captured["system"] = system
        captured["user"] = user
        return "# 비교 보고서"

    monkeypatch.setattr(comp.gemini_sync, "generate", fake_generate)

    row = comp.enqueue_comparison(db, sid, 2026, ["KR", "US"])
    await comp.process_comparison(db, row)

    p = captured["user"]
    assert "대조표" in p
    assert "한국 보고서" in p and "미국 보고서" in p
    assert "차세대 메모리반도체" in p and "2026" in p   # 무엇을 비교하는지 헤더로 고정
    assert "세부내용-누출감지용" not in p               # sections_json이 새어들지 않았다
    assert row.status == "done"
    assert row.source_count == 2
    assert row.report_md == "# 비교 보고서"
