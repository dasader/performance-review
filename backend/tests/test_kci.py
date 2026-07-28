import httpx
import pytest

import app.clients.kci as kci
from app.clients.kci import _parse_search_xml
from app.config import settings

XML = """<MetaData><outputData>
<record>
  <journalInfo><journal-name>한국반도체학회지</journal-name><pub-year>2025</pub-year></journalInfo>
  <articleInfo article-id="ART001">
    <title-group>
      <article-title lang="original">고대역폭 메모리</article-title>
      <article-title lang="english">High Bandwidth Memory</article-title>
    </title-group>
    <abstract-group>
      <abstract lang="english">We present a TSV process.</abstract>
    </abstract-group>
    <doi>http://dx.doi.org/10.1234/abc</doi>
    <citation-count kci="3">3</citation-count>
  </articleInfo>
</record>
<record>
  <journalInfo><journal-name>J</journal-name><pub-year>2025</pub-year></journalInfo>
  <articleInfo article-id="ART002">
    <title-group><article-title lang="english">No Abstract</article-title></title-group>
  </articleInfo>
</record>
</outputData></MetaData>"""


def test_parse_prefers_english_and_flags_korea():
    papers = _parse_search_xml(XML)
    assert len(papers) == 2
    p = papers[0]
    assert p["paper_key"] == "10.1234/abc"
    assert p["title"] == "High Bandwidth Memory"
    assert p["abstract"] == "We present a TSV process."
    assert p["year"] == 2025
    assert p["citations"] == 3
    assert p["journal"] == "한국반도체학회지"
    assert p["korea_flag"] is True
    assert p["countries"] == ["KR"]
    assert p["source"] == "kci"


def test_parse_without_doi_uses_article_id_and_keeps_empty_abstract():
    """abstract 없는 논문도 파싱은 한다 — 검색 건수 통계에 필요하고, 제외는 filter 단계에서 한다."""
    papers = _parse_search_xml(XML)
    assert papers[1]["paper_key"] == "kci:ART002"
    assert papers[1]["abstract"] == ""


def _full_page_xml(n: int) -> str:
    """연도가 검색 범위 밖(1999)이라 전부 걸러지는 레코드 n건."""
    records = "".join(
        f'<record><journalInfo><journal-name>J</journal-name><pub-year>1999</pub-year></journalInfo>'
        f'<articleInfo article-id="ART{i}">'
        f'<title-group><article-title lang="english">T{i}</article-title></title-group>'
        f"</articleInfo></record>"
        for i in range(n)
    )
    return f"<MetaData><outputData>{records}</outputData></MetaData>"


async def test_search_stops_at_kci_max_pages(monkeypatch, caplog):
    """대상 연도 논문이 거의 없어도(전량 필터링), 페이지 상한(kci_max_pages)에서 멈춰야 한다."""
    monkeypatch.setattr(settings, "kci_api_key", "test-key")
    monkeypatch.setattr(settings, "kci_max_pages", 3)
    call_count = 0

    async def fake_get_with_retry(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, text=_full_page_xml(settings.kci_page_size))

    monkeypatch.setattr(kci, "get_with_retry", fake_get_with_retry)

    with caplog.at_level("WARNING"):
        papers = await kci.search("query", 2023, 2025, client=None, limit=1000)

    assert papers == []
    assert call_count == settings.kci_max_pages
    assert any("상한" in r.message for r in caplog.records)


def test_result_error_is_raised_not_swallowed():
    """KCI는 키 만료 등을 HTTP 200 + 본문 resultMsg로 알린다.

    이걸 "결과 0건"으로 삼키면 보고서가 국내지 성과를 0건으로 단정하게 된다.
    실측 응답 형태(2026-07, 키 만료 시).
    """
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<MetaData>
  <inputData><key>x</key><apiCode>articleSearch</apiCode></inputData>
  <outputData><result><resultMsg>사용기간이 종료되었습니다.</resultMsg></result></outputData>
</MetaData>"""
    with pytest.raises(kci.KciApiError) as e:
        kci._parse_search_xml(xml)
    assert "사용기간이 종료" in str(e.value)


def test_normal_response_does_not_raise():
    """정상 응답에는 result/resultMsg 블록이 없으므로 그대로 파싱돼야 한다."""
    assert len(kci._parse_search_xml(XML)) == 2


def test_parse_strips_html_tags_from_title_and_journal():
    """KCI 실응답에 태그가 섞이는지 확인은 못 했지만(키 만료), OpenAlex에서 실제로
    확인된 문제라 방어적으로 동일 헬퍼(strip_html)를 적용해뒀다 — 여기서 그 적용을
    검증한다. XML 안에서 진짜 텍스트로 태그가 들어오려면 이스케이프돼 있어야 한다
    (이스케이프 없이 그대로 `<sub>`를 쓰면 XML 파서가 자식 엘리먼트로 해석해버려
    `.text`가 잘려버린다 — 그래서 여기서도 `&lt;sub&gt;` 형태로 이스케이프한다)."""
    xml = """<MetaData><outputData>
<record>
  <journalInfo><journal-name>Journal &lt;i&gt;Name&lt;/i&gt;</journal-name><pub-year>2025</pub-year></journalInfo>
  <articleInfo article-id="ART003">
    <title-group>
      <article-title lang="english">Hf&lt;sub&gt;0.5&lt;/sub&gt;Zr&lt;sub&gt;0.5&lt;/sub&gt;O&lt;sub&gt;2&lt;/sub&gt; Film</article-title>
    </title-group>
  </articleInfo>
</record>
</outputData></MetaData>"""
    papers = _parse_search_xml(xml)
    # 태그가 원문에서 공백 없이 붙어 있었으므로(예: `Hf<sub>0.5</sub>`) 벗긴 뒤에도
    # 공백을 새로 만들어 넣지 않는다 — 없던 공백을 삽입하면 화학식 표기가 깨진다.
    assert papers[0]["title"] == "Hf0.5Zr0.5O2 Film"
    assert papers[0]["journal"] == "Journal Name"
