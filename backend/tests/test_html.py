from app.clients._html import strip_html


def test_strip_html_removes_tags_and_collapses_whitespace():
    assert strip_html("Hf <sub>0.5</sub> Zr <sub>0.5</sub> O <sub>2</sub>") == "Hf 0.5 Zr 0.5 O 2"


def test_strip_html_decodes_entities():
    assert strip_html("A &amp; B &lt;test&gt; &#x3B1;") == "A & B <test> α"


def test_strip_html_leaves_plain_title_untouched():
    assert strip_html("Plain Title Without Tags") == "Plain Title Without Tags"


def test_strip_html_handles_none_and_empty():
    assert strip_html(None) == ""
    assert strip_html("") == ""


def test_strip_html_does_not_insert_space_when_tag_was_flush_against_text():
    """실측 회귀: `MoS<sub>2</sub>/AlScN`처럼 태그 앞뒤에 원래 공백이 없던 경우, 태그
    자리에 공백을 새로 끼워 넣으면 안 된다(`MoS 2 /AlScN`은 틀림) — LLM은 이 화학식을
    `MoS2/AlScN`으로 붙여 쓰므로 공백을 넣으면 각주 매칭이 깨진다."""
    assert strip_html("MoS<sub>2</sub>/AlScN") == "MoS2/AlScN"
    assert strip_html("α‐In<sub>2</sub>Se<sub>3</sub> Ferroelectric") == "α‐In2Se3 Ferroelectric"


def test_strip_html_strips_mathml_jats_wrappers():
    # 실측: OpenAlex abstract 역색인 복원 시 단어에 JATS/MathML 래퍼가 그대로 붙어 온다.
    assert strip_html("<mml:math><mml:mi>x</mml:mi></mml:math> value") == "x value"
