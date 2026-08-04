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
    # 하위 항목이라 · 가 붙는다
    lines = [l for l in table.splitlines() if l.startswith("| · 아키텍처")]
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

    stats_json은 **dict**로 넣는다 — 컬럼이 JSON 타입이라 SQLAlchemy가 자동
    역직렬화하므로 읽을 때도 dict다. 여기에 문자열을 넣으면 실제와 달라져
    "코드에서 json.loads를 부르는" 버그를 테스트가 통과시킨다(실제로 그랬다).
    """
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
                stats_json=stats or _stats(),
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
    # 저장된 본문 = 코드가 끼운 대조표 + 모델 출력(_with_table).
    assert "# 비교 보고서" in row.report_md
    assert "| **모집단과 표본** |" in row.report_md


async def test_job_loop_processes_pending_comparison(monkeypatch):
    """비교 보고서도 잡 루프가 한 틱에 하나씩 처리한다.

    _process_report가 row.field_id를 로그에 직접 참조하면 여기서 AttributeError가
    난다 — 비교 행에는 field_id가 없다(subfield_id를 가진다)."""
    from app.services import runner

    db = _session()
    sid = _seed(db, ("KR", "US"))
    row = comparison.enqueue_comparison(db, sid, 2026, ["KR", "US"])

    called = {}

    async def fake_process(_db, r):
        called["id"] = r.id
        r.status = "done"
        _db.commit()

    monkeypatch.setattr(runner.comparison, "process_comparison", fake_process)
    await runner.advance_field_reports(db)

    assert called.get("id") == row.id
    assert row.status == "done"


async def test_job_loop_marks_failed_comparison_without_crashing(monkeypatch):
    """한 건의 실패가 루프 전체를 멈추지 않는다 — 그 행만 failed로 남는다."""
    from app.services import runner

    db = _session()
    sid = _seed(db, ("KR", "US"))
    row = comparison.enqueue_comparison(db, sid, 2026, ["KR", "US"])

    async def boom(_db, _r):
        raise RuntimeError("LLM 폭발")

    monkeypatch.setattr(runner.comparison, "process_comparison", boom)
    await runner.advance_field_reports(db)   # 예외가 밖으로 새면 안 된다

    db.refresh(row)
    assert row.status == "failed"
    assert "LLM 폭발" in (row.error or "")


def test_compare_instruction_separates_missing_abstracts_from_volume():
    """결측률 금지 조항이 과잉 적용되면 실재하는 발표량 차이까지 부정한다.

    실측(차세대 메모리반도체 2025 KR+CN 첫 생성): 모델이 "수집된 논문 수의 차이는
    abstract 보유율 차이에서 기인한다"고 썼는데 틀렸다 — 수집은 820 vs 304로
    abstract 필터링 이전에 이미 2.7배 차이가 났다. 결측은 820→731, 304→278 구간만
    설명한다. 금지를 약화시키지 말고 구간을 명시해 정밀화한다."""
    from app.prompts import COMPARE_INSTRUCTION

    assert "수집" in COMPARE_INSTRUCTION and "모집단" in COMPARE_INSTRUCTION
    # 결측이 설명하는 구간이 명시돼야 한다
    assert "구간" in COMPARE_INSTRUCTION


def test_comparison_table_groups_every_row_under_a_header():
    """모든 행이 그룹 아래에 놓이고, 하위 항목은 기호로 구분된다.

    사용자 신고: 귀속·성과유형에는 굵은 제목이 있는데 첫 블록(모집단·수집·표본율…)만
    제목이 없어 무엇의 묶음인지 알 수 없었다. 또 그룹 제목과 하위 항목이 같은 모양이라
    계층이 보이지 않았다.
    """
    rows = [("KR", _stats()), ("CN", _stats())]
    table = comparison.build_comparison_table(rows)
    body = [l for l in table.splitlines()[2:]]

    headers = [l for l in body if l.startswith("| **")]
    items = [l for l in body if l.startswith("| · ")]

    # 그룹은 넷: 모집단과 표본 · 연구 귀속 · 인용 · 성과유형
    assert len(headers) == 4
    assert any("모집단" in h for h in headers)
    # 그룹 제목이 아닌 행은 전부 하위 항목 기호를 단다 — 계층이 한눈에 보여야 한다
    assert len(headers) + len(items) == len(body)


def test_comparison_table_marks_the_base_on_the_group_not_the_item():
    """(수집 기준)·(분석 기준)은 그룹 제목에 붙는다 — 항목마다 반복하면 표가 시끄럽다."""
    table = comparison.build_comparison_table([("KR", _stats()), ("CN", _stats())])
    header_line = next(l for l in table.splitlines() if "귀속" in l)
    assert "수집 기준" in header_line
    assert "· 단독" in table and "수집 기준" not in table.split("· 단독")[1].split("\n")[0]


def test_compare_instruction_demands_coverage_of_every_achievement_type():
    """§3이 성과유형을 빠짐없이 다루도록 강제해야 한다.

    사용자 신고 + 실측(차세대 메모리반도체 2025 KR+CN): 대조표에 성과유형이 9개인데
    §3 서술에는 3개(공정·신소자·아키텍처)만 등장했다. 로드맵 전수 점검과 같은 부류로,
    개수를 못박지 않으면 모델이 대표 몇 개로 뭉갠다.
    """
    from app.prompts import COMPARE_INSTRUCTION

    assert "{type_count}" in COMPARE_INSTRUCTION
    assert "{type_list}" in COMPARE_INSTRUCTION


def test_payload_injects_the_achievement_types():
    """개수·목록을 코드가 세어 프롬프트에 박는다 — 모델에게 세라고 시키지 않는다."""
    rows = [
        ("KR", _stats(by_achievement_type={"신소자": 60, "공정": 29})),
        ("CN", _stats(by_achievement_type={"아키텍처": 300, "신소자": 96})),
    ]
    instruction = comparison.compare_instruction(rows)

    assert "3개" in instruction          # 합집합 3종
    for t in ("공정", "신소자", "아키텍처"):
        assert t in instruction
    assert "{type_count}" not in instruction and "{type_list}" not in instruction


async def test_table_is_inserted_by_code_not_the_model(monkeypatch):
    """대조표는 코드가 본문에 끼워 넣는다 — 모델에게 베끼게 하지 않는다.

    실측으로 두 번 실패했다: 처음엔 모델이 형식을 바꿔 실었고(그룹 제목·계층 기호가
    사라짐), 프롬프트를 고치자 이번엔 표를 통째로 빼고 서술만 했다. 코드가 이미 갖고
    있는 것을 모델에게 왕복시킬 이유가 없다.
    """
    from app.services import comparison as comp

    db = _session()
    sid = _seed(db, ("KR", "US"))

    async def fake_generate(system, user, *, thinking=None, **kw):
        # 모델은 표 없이 절만 쓴다
        return "## 1. 비교 개요\n서술입니다.\n\n## 2. 연구 규모와 구조\n본문."

    monkeypatch.setattr(comp.gemini_sync, "generate", fake_generate)
    row = comp.enqueue_comparison(db, sid, 2026, ["KR", "US"])
    await comp.process_comparison(db, row)

    md = row.report_md
    assert "| **모집단과 표본** |" in md          # 표가 들어갔다
    assert "| · 표본율 |" in md                   # 계층 기호도 그대로
    # 표는 1절 제목 바로 뒤에 온다 — 서술보다 앞이라 조건을 먼저 보게 된다
    first, rest = md.split("## 1. 비교 개요", 1)
    assert rest.index("| **모집단과 표본** |") < rest.index("서술입니다")


async def test_table_is_prepended_when_the_model_omits_the_heading(monkeypatch):
    """1절 제목을 못 찾으면 맨 앞에 붙인다 — 표가 사라지는 경우는 없어야 한다."""
    from app.services import comparison as comp

    db = _session()
    sid = _seed(db, ("KR", "US"))

    async def fake_generate(system, user, *, thinking=None, **kw):
        return "제목 없이 그냥 서술만 한 보고서."

    monkeypatch.setattr(comp.gemini_sync, "generate", fake_generate)
    row = comp.enqueue_comparison(db, sid, 2026, ["KR", "US"])
    await comp.process_comparison(db, row)

    assert "| **모집단과 표본** |" in row.report_md


def test_comparison_holds_pairwise_sections():
    """쌍별 보고서를 보관한다. analyses.sections_json과 같은 모양이다."""
    db = _session()
    row = CountryComparison(
        subfield_id=1, year=2026, countries="CN,KR,US",
        generated_at=datetime(2026, 8, 4),
        sections_json=[{"name": "한국 vs 미국", "body": "본문"}],
    )
    db.add(row)
    db.commit()

    saved = db.query(CountryComparison).one()
    assert saved.sections_json[0]["name"] == "한국 vs 미국"


def test_sections_default_is_empty_list():
    """기본값이 None이면 화면이 length를 읽다 터진다."""
    db = _session()
    db.add(CountryComparison(subfield_id=1, year=2026, countries="CN,KR",
                             generated_at=datetime(2026, 8, 4)))
    db.commit()
    assert db.query(CountryComparison).one().sections_json == []


def test_pairs_are_against_korea_regardless_of_sort_order():
    """countries는 정렬 저장이라 KR이 가운데 온다(CN,KR,US). 첫 원소를 기준국으로
    삼으면 '중국 vs 미국'이 되어 '한국과의 비교'라는 목적을 잃는다."""
    assert comparison.pair_countries(["CN", "KR", "US"]) == [("KR", "CN"), ("KR", "US")]


def test_pairs_fall_back_to_first_when_korea_absent():
    assert comparison.pair_countries(["CN", "US"]) == [("CN", "US")]


def test_two_countries_make_a_single_pair():
    assert comparison.pair_countries(["CN", "KR"]) == [("KR", "CN")]


def test_synthesis_instruction_forbids_repeating_the_pairwise_bodies():
    """종합이 쌍별 내용을 다시 쓰면 그게 곧 축약 압력이 된다 — 쌍별 상세가 이미
    보관되므로 종합은 국가를 가로지르는 관찰만 한다."""
    from app.prompts import COMPARE_SYNTHESIS_INSTRUCTION

    assert "반복" in COMPARE_SYNTHESIS_INSTRUCTION
    assert "가로질러" in COMPARE_SYNTHESIS_INSTRUCTION
    # 쌍별과 같은 금지 조항을 공유한다(길이·표본율·순위)
    for word in ("길이", "표본율", "순위"):
        assert word in COMPARE_SYNTHESIS_INSTRUCTION


async def test_three_countries_produce_pairwise_sections_and_a_synthesis(monkeypatch):
    """3개국이면 쌍별 2건 + 종합 1콜 = 3콜. 쌍별은 sections_json에 남는다."""
    from app.services import comparison as comp

    db = _session()
    sid = _seed(db, ("KR", "US", "CN"))

    calls = []

    async def fake_generate(system, user, *, thinking=None, **kw):
        calls.append(user)
        return f"# 결과 {len(calls)}"

    monkeypatch.setattr(comp.gemini_sync, "generate", fake_generate)
    row = comp.enqueue_comparison(db, sid, 2026, ["KR", "US", "CN"])
    await comp.process_comparison(db, row)

    assert len(calls) == 3
    assert [s["name"] for s in row.sections_json] == ["한국 vs 중국", "한국 vs 미국"]
    # 종합 입력에는 쌍별 결과가 들어간다
    assert "결과 1" in calls[-1] and "결과 2" in calls[-1]
    assert row.status == "done"


async def test_two_countries_skip_the_synthesis(monkeypatch):
    """쌍이 하나뿐이면 그것이 곧 보고서다 — 종합을 건너뛰어 현행 비용을 유지한다."""
    from app.services import comparison as comp

    db = _session()
    sid = _seed(db, ("KR", "US"))
    calls = []

    async def fake_generate(system, user, *, thinking=None, **kw):
        calls.append(user)
        return "# 쌍별 결과"

    monkeypatch.setattr(comp.gemini_sync, "generate", fake_generate)
    row = comp.enqueue_comparison(db, sid, 2026, ["KR", "US"])
    await comp.process_comparison(db, row)

    assert len(calls) == 1
    assert row.sections_json == []          # 펼칠 것이 없다
    assert "쌍별 결과" in row.report_md
