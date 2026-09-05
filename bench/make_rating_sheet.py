#!/usr/bin/env python3
"""사람 평가자용 로드맵 판정 기록지 생성.

LLM 판정의 타당성을 재려면 **사람이 같은 입력으로 같은 판정을 내린 것**이 필요하다.
그래서 이 기록지는 세 가지를 지킨다.

  ① **맹검** — LLM이 뭐라고 했는지 절대 싣지 않는다. 보여주면 앵커링으로 측정이 죽는다.
  ② **같은 입력** — 모델이 본 (B) 세부기술 보고서 전량을 사람도 본다. 모델이 못 본 것을
     사람이 보고 "모델이 틀렸다"고 하면 그건 검증이 아니다.
  ③ **3지 척도** — 이 분야는 세부기술 보고서가 전부 있어 `분석 범위 밖`이 성립하지
     않는다(코드가 판정). 사람에게도 같은 선택지를 준다.

    PYTHONPATH=backend backend/.venv/bin/python bench/make_rating_sheet.py
    PYTHONPATH=backend backend/.venv/bin/python bench/make_rating_sheet.py --field 미래에너지
"""
import argparse
import html
import importlib.util
import sys
from pathlib import Path

import psycopg2

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("rp", REPO / "bench" / "roadmap_panel.py")
_rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rp)

CSS = """
:root { --ink:#1a1a1a; --mut:#666; --line:#d4d4d4; --accent:#1d4ed8; }
* { box-sizing:border-box; }
body { font-family:'Noto Sans KR',-apple-system,'Malgun Gothic',sans-serif;
       color:var(--ink); line-height:1.55; margin:0; padding:28px 32px; max-width:980px; }
h1 { font-size:22px; margin:0 0 4px; letter-spacing:-.02em; }
h2 { font-size:16px; margin:28px 0 10px; padding-bottom:6px; border-bottom:2px solid var(--ink); }
h3 { font-size:14px; margin:18px 0 6px; color:var(--accent); }
.sub { color:var(--mut); font-size:13px; margin:0 0 18px; }
.box { border:1px solid var(--line); padding:14px 16px; margin:14px 0; font-size:13px; background:#fafafa; }
.box b { color:var(--accent); }
table { border-collapse:collapse; width:100%; font-size:12.5px; }
th,td { border:1px solid var(--line); padding:7px 8px; vertical-align:top; text-align:left; }
th { background:#f0f0f0; font-weight:600; font-size:12px; }
.n  { width:30px; text-align:center; color:var(--mut); }
.st { width:78px; }
.pick { width:186px; font-size:11.5px; line-height:1.9; white-space:nowrap; }
.why { width:210px; }
.grp { background:#eef2ff; font-weight:600; font-size:12.5px; }
.rep { font-size:12px; white-space:pre-wrap; border:1px solid var(--line);
       padding:12px 14px; margin:0 0 16px; }
.rep h4 { margin:0 0 8px; font-size:13.5px; color:var(--accent); }
@media print {
  body { padding:0; max-width:none; font-size:11px; }
  h2 { page-break-before:always; }
  h2:first-of-type { page-break-before:avoid; }
  tr, .rep { page-break-inside:avoid; }
  .box { background:none; }
}
"""

HOWTO = """<div class="box">
<b>기록 방법</b><br>
1. 먼저 <b>2부 근거 자료(세부기술 보고서 {nrep}건)</b>를 훑어 두세요. 모델도 이것만 보고 판정했습니다.<br>
2. 각 행마다 판정 하나에 표시하고, <b>근거 칸에 (B)의 어느 서술을 보았는지</b> 적으세요.<br>
&nbsp;&nbsp;&nbsp;`데이터 없음`이면 <i>무엇을 찾았는데 없었는지</i>를 적습니다.<br>
3. 앞 행의 판정에 끌리지 말고 <b>행마다 독립적으로</b> 판단하세요.<br>
4. 헷갈린 행에는 <b>★</b>를 남겨 주세요 — 사람에게도 어려운 행이 어디인지가 그 자체로 결과입니다.<br>
5. 총 소요 시간을 적어 주세요: ________분<br><br>
<b>판정 셋</b><br>
· <b>관련 연구 확인</b> — 목표와 <b>직접 맞닿는</b> 연구 성과가 (B)에 있음<br>
· <b>부분 관련</b> — 인접 주제 연구는 있으나 목표가 요구하는 <b>수준·대상과 어긋남</b><br>
· <b>데이터 없음</b> — (B)에 근거가 없음<br><br>
<b>주의</b> — 논문 성과는 "연구가 진행되고 있다"는 신호일 뿐 <b>목표 달성의 증거가 아닙니다.</b>
근거 없는 목표가 많은 것은 정상이며, 그것을 그대로 드러내는 것이 이 점검의 목적입니다.<br>
(`분석 범위 밖`은 선택지에 없습니다 — 이 분야는 세부기술 보고서가 전부 있어 성립하지 않고,
그 판정은 코드가 합니다.)
</div>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="반도체")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--country", default="KR")
    ap.add_argument("--rater", default="", help="평가자 이름(머리말에 표시)")
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    cur = psycopg2.connect(a.dsn).cursor()
    cur.execute("SELECT id, name FROM fields WHERE name LIKE %s", (a.field + "%",))
    fid, fname = cur.fetchone()
    cur.execute("SELECT content_md FROM roadmaps WHERE field_id = %s", (fid,))
    goals = _rp.parse_goals(cur.fetchone()[0])
    cur.execute("""
        SELECT s.name, a.report_md FROM analyses a JOIN subfields s ON s.id = a.subfield_id
        WHERE s.field_id = %s AND a.year = %s AND a.country = %s AND a.status='done'
          AND a.report_md IS NOT NULL AND a.report_md <> '' ORDER BY s.name
    """, (fid, a.year, a.country))
    reports = cur.fetchall()

    e = html.escape
    rows, last = [], None
    for g in goals:
        if g["중점기술"] != last:
            last = g["중점기술"]
            rows.append(f'<tr class="grp"><td colspan="5">{e(last)}</td></tr>')
        item = f'<div style="color:#666;font-size:11px">{e(g["세부항목"])}</div>' \
               if g["세부항목"] != g["중점기술"] else ""
        rows.append(
            f'<tr><td class="n">{g["id"]}</td>'
            f'<td class="st">{e(g["단계"])}'
            + (f'<div style="color:#666;font-size:11px">{e(g["시기"])}</div>' if g["시기"] else "")
            + '</td>'
            f'<td>{item}{e(g["목표"])}</td>'
            f'<td class="pick">☐ 관련 연구 확인<br>☐ 부분 관련<br>☐ 데이터 없음<br>'
            f'<span style="color:#999">☐ ★ 헷갈림</span></td>'
            f'<td class="why"></td></tr>')

    reps = "".join(
        f'<div class="rep"><h4>{e(n)}</h4>{e(md)}</div>' for n, md in reports)

    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>{e(fname)} {a.year} 로드맵 판정 기록지</title><style>{CSS}</style></head><body>
<h1>{e(fname)} {a.year}년 로드맵 이행 점검 — 사람 판정 기록지</h1>
<p class="sub">평가자: {e(a.rater) or "________________"} &nbsp;·&nbsp; 판정일: ____ / ____ &nbsp;·&nbsp;
목표 {len(goals)}행 &nbsp;·&nbsp; 근거 자료 {len(reports)}건 &nbsp;·&nbsp; 대상국 {e(a.country)}
&nbsp;·&nbsp; <b>다른 평가자와 상의하지 마세요</b></p>
{HOWTO.format(nrep=len(reports))}
<h2>1부. 판정 기록 ({len(goals)}행)</h2>
<table><thead><tr><th class="n">#</th><th class="st">단계·구분<br><span style="font-weight:400;color:#666">(시기)</span></th>
<th>기술적 목표 (A)</th><th class="pick">판정</th><th class="why">근거 — (B)의 어느 서술인가</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>2부. 근거 자료 (B) — 세부기술별 성과 보고서 {len(reports)}건</h2>
<p class="sub">모델이 판정에 사용한 것과 <b>동일한 자료</b>입니다. 이 밖의 지식으로 판정하지 마세요.</p>
{reps}
</body></html>"""

    out = Path(a.out or REPO / "bench" / f"판정기록지-{a.field}-{a.year}.html")
    out.write_text(doc, encoding="utf-8")

    # 종이에 적은 것을 다시 입력할 때 쓰는 서식. 채점 스크립트가 이 파일을 그대로 읽는다.
    tpl = Path(str(out).replace(".html", "-입력서식.txt"))
    lines = [
        f"# {fname} {a.year} {a.country} — 판정 입력 서식",
        "# 평가자: ____________   소요시간: ____분",
        "# 형식: 번호 | 판정 | 헷갈림(★ 또는 공란) | 근거",
        "#   판정은  확인 / 부분 / 없음  셋 중 하나만 (약칭 그대로 쓰세요)",
        "",
    ]
    for g in goals:
        lines.append(f"{g['id']:>2} |      |   | ")
        lines.append(f"#      ↑ [{g['단계']}] {g['목표'][:70]}")
    tpl.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"목표 {len(goals)}행 · 보고서 {len(reports)}건")
    print(f"  기록지  → {out}")
    print(f"  입력서식 → {tpl}")


if __name__ == "__main__":
    main()
