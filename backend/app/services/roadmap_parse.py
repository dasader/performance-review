"""로드맵 마크다운 파서. **app.config를 import하지 않는다** — bench 스크립트가
`Settings`(extra 금지) 없이 이 모듈만 가져다 쓸 수 있어야 같은 규칙을 두 번 구현하지 않는다.
"""
import re

_SEP = re.compile(r"^\|[\s:|-]+\|$")


def parse_goals(md: str) -> list[dict]:
    """로드맵 마크다운에서 목표 행을 뽑는다.

    **열 위치를 가정하지 않고 머리행으로 찾는다.** 이 로드맵에는 표가 두 모양이 있다:

        | 단계 | 시기 | 기술적 목표 |    ← 시간 축이 있다 (1단계 ~'25년 …)
        | 구분 | 기술적 목표 |           ← 첨단패키징 등. 첫 열이 시간이 아니라 항목명

    위치로 읽으면 후자에서 `시기` 칸에 목표 텍스트가 중복으로 들어간다(실측 20/65행).

    `단계축`은 **이 행에 시간 축이 있는가**다. 머리행이 `단계`이고 값이 `N단계` 꼴일 때만
    참이며, 실측 65행 중 45행뿐이다. 단계별 집계는 반드시 이 표시로 걸러야 한다 —
    걸르지 않으면 병렬 항목(해석기술·인터포저 등)이 없는 단계에 배정된다.
    """
    goals: list[dict] = []
    section = heading = ""
    in_table = False
    hdr: list[str] = []
    prev: list[str] = []          # 구분선 바로 앞 줄 = 머리행

    for line in md.splitlines():
        s = line.strip()
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            text = s.lstrip("#").strip()
            # `## N. 이름`이 중점기술, 그 아래 ###/####가 세부 항목이다.
            # 상위를 버리면 "MRAM" 한 단어만 남아 맥락이 사라진다.
            if level <= 2:
                section, heading = text, ""
            else:
                heading = text
            in_table, prev = False, []
            continue
        if not s.startswith("|"):
            in_table, prev = False, []
            continue

        cells = [c.strip() for c in s.strip("|").split("|")]
        if _SEP.match(s):
            in_table, hdr = True, prev
            continue
        if not in_table:
            prev = cells
            continue

        def col(*names, default=None):
            for i, h in enumerate(hdr):
                if any(n in h for n in names):
                    return i
            return default

        i_stage = col("단계", "구분", default=0)
        i_goal = col("목표", default=len(cells) - 1)
        i_time = col("시기")
        pick = lambda i: cells[i] if i is not None and i < len(cells) else ""  # noqa: E731
        stage = pick(i_stage)
        goals.append({
            "id": len(goals) + 1,
            "중점기술": section,
            "세부항목": heading or section,
            "단계": stage,
            "시기": pick(i_time),
            "단계축": bool(hdr and i_stage < len(hdr) and "단계" in hdr[i_stage]
                          and re.fullmatch(r"\d+단계", stage)),
            "목표": pick(i_goal),
        })
    return goals


def count_goal_rows(md: str) -> int:
    """표 본문 행 수. `parse_goals`와 같은 규칙이므로 둘은 항상 같은 값을 낸다."""
    return len(parse_goals(md))
