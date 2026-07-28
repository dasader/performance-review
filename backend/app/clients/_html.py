"""OpenAlex/KCI가 논문 제목·초록에 HTML/MathML 태그를 그대로 섞어 보낼 때가 있다.
실측(OpenAlex): `Hf <sub>0.5</sub> Zr <sub>0.5</sub> O <sub>2</sub>`, `<i>in situ</i>`,
JATS 수식 래퍼(`<mml:math>`, `<inline-formula>`, `<tex-math>` 등)까지 다양해 태그별로
따로 다룰 수 없다 — `<...>` 형태는 전부 벗기고 엔티티만 디코딩한다."""

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str | None) -> str:
    """실제 태그(`<...>`)를 먼저 벗기고 그다음 엔티티를 디코딩한 뒤 연속 공백을 하나로
    접는다. 태그 제거를 엔티티 디코딩보다 먼저 해야, 텍스트에 `&lt;i&gt;`처럼 이스케이프된
    형태로 들어있는(= 실제 태그가 아닌, 문자 그대로 보여주려는) 문자열까지 디코딩 후
    태그로 오인해 지워버리는 걸 막을 수 있다.

    태그는 빈 문자열로 치환한다(공백 삽입 아님) — OpenAlex 원문은 태그 앞뒤에 공백이
    있을 때도(`Hf <sub>0.5</sub> Zr` -> `Hf 0.5 Zr`) 없을 때도(`MoS<sub>2</sub>/AlScN`
    -> `MoS2/AlScN`, `In<sub>2</sub>Se<sub>3</sub>` -> `In2Se3`) 있다. 태그 자리에
    공백을 끼워 넣으면 후자(화학식처럼 붙어있던 경우)에 없던 공백이 생겨버려 LLM이
    실제로 쓰는 표기(`In2Se3`)와 달라지고 각주 매칭이 깨진다. 이미 있던 공백은 원문
    문자 그대로 남고, 그 연속만 이 함수가 하나로 접는다."""
    if not text:
        return text or ""
    no_tags = _TAG_RE.sub("", text)
    return " ".join(html.unescape(no_tags).split())
