from app.clients.kci import _parse_search_xml

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
