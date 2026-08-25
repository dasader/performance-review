#!/usr/bin/env python3
"""achievement_type 프롬프트 A/B — 자기 일치율이 오르는가.

**재는 것은 "정답률"이 아니라 "자기 일치율"이다.** 성과유형에는 정답지가 없다.
문제는 같은 논문·같은 프롬프트로 두 번 돌리면 17%가 다른 값이 나온다는 것이고
(현행 자기 일치 0.830, 19,904쌍 실측), 그 흔들림이 3단 reduce의 그룹 분할과
stats.by_achievement_type을 흔든다. 그래서 각 변형을 **같은 논문에 두 번** 돌려
자기 자신과의 일치율을 재고, 변형끼리 비교한다.

세 변형으로 나눈 이유(ablation): 한 번에 둘을 바꾸면 어느 쪽이 일했는지 모른다.

  A  현행 MAP_INSTRUCTION 그대로 (기준선)
  B  enum에서 `성능향상`만 제거 + 그 이유 한 줄
  C  B + 결정 리스트(질문을 순서대로 묻고 처음 "예"에서 멈춤)

표본 크기: n=60이면 p≈0.83에서 95% 신뢰구간이 ±9.6%p라 0.83과 0.90을 구분하지
못한다. 기본값을 150으로 둔 이유다(±6.0%p). 호출 비용은 150×3변형×2회=900콜로
약 $0.36이라 표본을 아끼는 것이 더 비싸다.

주의: 운영 추출은 Batch API를 쓰지만 여기서는 반복 실행 편의를 위해 sync를 쓴다.
모델·thinking·스키마는 운영과 같게 맞췄으므로 프롬프트 효과가 지배적이라고 보지만,
경로가 다르다는 점은 결과 해석에 남겨 둔다.

    PYTHONPATH=backend backend/.venv/bin/python bench/achievement_type_ab.py --n 150
"""
import argparse
import asyncio
import json
import statistics  # noqa: F401  (결과 확장 시 사용)
import sys
import time
from collections import Counter
from pathlib import Path

import psycopg2
from google import genai
from google.genai import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from app.prompts import MAP_INSTRUCTION, MAP_SCHEMA, map_user_text  # noqa: E402

MODEL = "gemini-3.1-flash-lite"   # 운영 gemini_model과 동일
THINKING = "low"                  # 운영 THINKING_MAP과 동일

# ── 변형 정의 ────────────────────────────────────────────────────────────────
# 현행 지시문에서 성과유형을 다루는 딱 한 줄. 이 줄만 갈아끼운다 — 다른 곳을 함께
# 건드리면 무엇이 효과를 냈는지 알 수 없다.
CURRENT_LINE = (
    "- achievement_type: 신소자, 신소재, 공정, 알고리즘, 아키텍처, "
    "성능향상, 시스템구현, 이론/해석, 기타 중 하나."
)

DROP_PERF_LINE = """- achievement_type: 신소자, 신소재, 공정, 알고리즘, 아키텍처,
  시스템구현, 이론/해석, 기타 중 하나.
  "성능이 향상되었다"는 것은 성과유형이 아닙니다 — 무엇을 만들어서 향상시켰는지로
  고르고, 향상 내용 자체는 improvement에 적으세요."""

DECISION_LIST_LINE = """- achievement_type: 아래 질문을 **순서대로** 묻고, 처음으로 "예"가
  나오는 유형을 고르세요. **둘 이상에 해당하는 논문이 많습니다 — 그럴 때는 앞선 것이
  이깁니다.** 뒤의 것이 더 어울려 보여도 순서를 지키세요.
  1. 새로운 물질·조성·소재를 만들었나? → 신소재
  2. 새로운 소자·디바이스를 만들었나? → 신소자
  3. 제조·합성·가공·측정 방법을 개선했나? → 공정
  4. 동작하는 시스템·플랫폼·장비를 구축해 실증했나? → 시스템구현
  5. 모델·회로·네트워크의 구조를 새로 설계했나? → 아키텍처
  6. 계산 절차·학습 방법·제어 방법을 제안했나? → 알고리즘
  7. 현상을 규명·해석했거나 이론·모형을 제시했나?
     (리뷰·서베이·동향 분석도 여기) → 이론/해석
  8. 위 어디에도 맞지 않으면 → 기타
  "성능이 향상되었다"는 것은 성과유형이 아닙니다 — 무엇을 만들어서 향상시켰는지로
  고르고, 향상 내용 자체는 improvement에 적으세요."""

# D·E — C에 남은 혼동을 겨냥한다. 실측(n=400, C의 자기불일치 35건)에서 남은 혼동은
# **딥러닝·소프트웨어 논문에서 아키텍처/알고리즘/시스템구현 셋이 동시에 참**인 데서
# 나왔다(아키텍처↔알고리즘 7, 시스템구현↔아키텍처 6, 시스템구현↔알고리즘 5 = 51%).
# 흔들린 논문은 IEL-YOLO(YOLOv11 기반) · Siamese network framework ·
# LLM-based Evaluation Agent 같은 것들이다. 이 코퍼스는 AI 논문 비중이 높아
# (알고리즘+시스템구현+아키텍처 = 약 47%) 이 경계가 곧 최대 변동원이다.
#
# D: 경계에 판정 규칙을 넣는다(분류 체계는 그대로).
# E: 아키텍처를 알고리즘에 흡수한다(체계를 줄인다).
#    — 존재하지 않는 범주는 혼동될 수 없다. 대신 정보를 잃고, 알고리즘 그룹이
#      코퍼스의 약 3분의 1이 되어 3단 reduce에서 그룹 재분할(미검증 경로)에 걸린다.
DECISION_LIST_D = """- achievement_type: 아래 질문을 **순서대로** 묻고, 처음으로 "예"가
  나오는 유형을 고르세요. **둘 이상에 해당하는 논문이 많습니다 — 그럴 때는 앞선 것이
  이깁니다.** 뒤의 것이 더 어울려 보여도 순서를 지키세요.
  1. 새로운 물질·조성·소재를 만들었나? → 신소재
     (물질 자체가 새로운 경우. 알려진 물질을 새 방법으로 만든 것은 3번으로.)
  2. 새로운 소자·디바이스를 만들었나? → 신소자
  3. 제조·합성·가공·측정 방법을 개선했나? → 공정
  4. 동작하는 시스템·플랫폼·장비를 구축해 실증했나? → 시스템구현
     (여러 구성요소를 통합해 실제 환경에서 운용한 경우. 모델이나 알고리즘 하나를
      구현해 성능을 평가한 것만으로는 여기에 해당하지 않습니다 — 6번으로.)
  5. 모델·회로·네트워크의 **구조 자체**를 새로 설계했나? → 아키텍처
     (새로운 층·블록·연결 방식·회로 토폴로지를 제안한 경우. 기존 구조
      (YOLO·ResNet·Transformer 등)를 가져다 쓰거나 조합·미세조정한 것은 6번으로.)
  6. 계산 절차·학습 방법·제어 방법을 제안했나? → 알고리즘
     (기존 구조를 쓴 학습·최적화·전처리·앙상블·파이프라인도 여기입니다.)
  7. 현상을 규명·해석했거나 이론·모형을 제시했나? → 이론/해석
     (수학적 해석·시뮬레이션 분석·리뷰·서베이. 다만 새로운 방법을 제안했다면
      6번이 우선입니다.)
  8. 위 어디에도 맞지 않으면 → 기타
  "성능이 향상되었다"는 것은 성과유형이 아닙니다 — 무엇을 만들어서 향상시켰는지로
  고르고, 향상 내용 자체는 improvement에 적으세요."""

DECISION_LIST_E = """- achievement_type: 아래 질문을 **순서대로** 묻고, 처음으로 "예"가
  나오는 유형을 고르세요. **둘 이상에 해당하는 논문이 많습니다 — 그럴 때는 앞선 것이
  이깁니다.** 뒤의 것이 더 어울려 보여도 순서를 지키세요.
  1. 새로운 물질·조성·소재를 만들었나? → 신소재
     (물질 자체가 새로운 경우. 알려진 물질을 새 방법으로 만든 것은 3번으로.)
  2. 새로운 소자·디바이스를 만들었나? → 신소자
  3. 제조·합성·가공·측정 방법을 개선했나? → 공정
  4. 동작하는 시스템·플랫폼·장비를 구축해 실증했나? → 시스템구현
     (여러 구성요소를 통합해 실제 환경에서 운용한 경우. 모델이나 알고리즘 하나를
      구현해 성능을 평가한 것만으로는 여기에 해당하지 않습니다 — 5번으로.)
  5. 계산 절차·모델 구조·학습 방법·제어 방법을 제안했나? → 알고리즘
     (신경망 구조, 회로 토폴로지, 학습·최적화·전처리·앙상블 전부 여기입니다.)
  6. 현상을 규명·해석했거나 이론·모형을 제시했나? → 이론/해석
     (수학적 해석·시뮬레이션 분석·리뷰·서베이. 다만 새로운 방법을 제안했다면
      5번이 우선입니다.)
  7. 위 어디에도 맞지 않으면 → 기타
  "성능이 향상되었다"는 것은 성과유형이 아닙니다 — 무엇을 만들어서 향상시켰는지로
  고르고, 향상 내용 자체는 improvement에 적으세요."""

TYPES_NO_PERF = [t for t in MAP_SCHEMA["properties"]["achievement_type"]["enum"]
                 if t != "성능향상"]
TYPES_E = [t for t in TYPES_NO_PERF if t != "아키텍처"]


def _schema(types_list: list[str]) -> dict:
    s = json.loads(json.dumps(MAP_SCHEMA))       # 깊은 복사 — 원본을 건드리지 않는다
    s["properties"]["achievement_type"]["enum"] = types_list
    return s


def _instruction(new_line: str) -> str:
    assert CURRENT_LINE in MAP_INSTRUCTION, "지시문의 성과유형 줄을 찾지 못했습니다"
    return MAP_INSTRUCTION.replace(CURRENT_LINE, new_line)


VARIANTS = {
    "A_현행": (MAP_INSTRUCTION, MAP_SCHEMA),
    "B_성능향상제거": (_instruction(DROP_PERF_LINE), _schema(TYPES_NO_PERF)),
    "C_결정리스트": (_instruction(DECISION_LIST_LINE), _schema(TYPES_NO_PERF)),
    "D_경계규칙": (_instruction(DECISION_LIST_D), _schema(TYPES_NO_PERF)),
    "E_아키텍처흡수": (_instruction(DECISION_LIST_E), _schema(TYPES_E)),
}


# ── 호출 ─────────────────────────────────────────────────────────────────────

async def one(client, sem, instruction, schema, title, abstract):
    cfg = types.GenerateContentConfig(
        system_instruction=instruction,
        response_mime_type="application/json",
        response_schema=schema,
        thinking_config=types.ThinkingConfig(thinking_level=THINKING),
        max_output_tokens=16000,
    )

    def call():
        return client.models.generate_content(
            model=MODEL, contents=map_user_text(title, abstract), config=cfg)

    async with sem:
        for attempt in range(4):
            try:
                r = await asyncio.get_running_loop().run_in_executor(None, call)
                return json.loads(r.text or "{}")
            except Exception as e:
                if attempt == 3:
                    return {"_err": f"{type(e).__name__}: {e}"[:120]}
                await asyncio.sleep(2 ** attempt)


async def pass_over(client, sem, instruction, schema, papers, label):
    t0 = time.monotonic()
    out = await asyncio.gather(*[
        one(client, sem, instruction, schema, p["title"], p["abstract"]) for p in papers
    ])
    print(f"    {label}: {len(papers)}건 {time.monotonic() - t0:.0f}초", flush=True)
    return out


# ── 집계 ─────────────────────────────────────────────────────────────────────

def agreement(a: list[dict], b: list[dict]) -> tuple[int, int, Counter]:
    ok = same = 0
    conf = Counter()
    for x, y in zip(a, b):
        tx, ty = x.get("achievement_type"), y.get("achievement_type")
        if not tx or not ty:
            continue
        ok += 1
        if tx == ty:
            same += 1
        else:
            conf[tuple(sorted((tx, ty)))] += 1
    return same, ok, conf


def mcnemar(a_ok: list[bool], c_ok: list[bool]) -> dict:
    """짝지은 이항 비교. **독립 신뢰구간을 겹쳐 보면 안 된다** — 같은 논문에 두 변형을
    돌렸으므로 논문별로 짝지어 비교해야 검정력이 나온다(실측: n=150에서 A 0.813 대
    C 0.867의 독립 CI는 겹쳤지만, 그것은 차이가 없다는 뜻이 아니라 잘못된 자로 잰
    것이다).

    b = A만 자기일치, c = C만 자기일치. 둘 다 같은 쪽은 정보가 없어 버린다.
    정규근사 대신 이항 정확검정을 쓴다 — 불일치 쌍 수가 작을 수 있다.
    """
    b = sum(1 for x, y in zip(a_ok, c_ok) if x and not y)
    c = sum(1 for x, y in zip(a_ok, c_ok) if y and not x)
    n = b + c
    if n == 0:
        return {"b(A만)": 0, "c(C만)": 0, "p": 1.0, "판정": "차이 없음"}
    # 양측 이항 정확검정 p = P(|X - n/2| >= |c - n/2|), X~B(n, 0.5)
    from math import comb
    k = min(b, c)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / (2 ** n))
    return {
        "b(A만 일치)": b, "c(C만 일치)": c, "불일치쌍": n,
        "p": round(p, 4),
        "판정": "유의(p<0.05)" if p < 0.05 else "유의하지 않음",
    }


def wilson(k: int, n: int) -> tuple[float, float]:
    """Wilson 95% 신뢰구간. n이 작을 때 정규근사보다 낫고, 표본이 작다는 사실을
    결과에 같이 실어 두려고 쓴다."""
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (round(max(0.0, c - h), 3), round(min(1.0, c + h), 3))


def fetch(n: int, dsn: str) -> list[dict]:
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("""
        SELECT p.title, p.abstract, e.achievement_type
        FROM papers p JOIN paper_extractions e ON e.paper_key = p.paper_key
        WHERE p.abstract <> '' AND e.model_ver LIKE %s
        ORDER BY md5(p.paper_key) LIMIT %s
    """, ("%v3", n))
    rows = [{"title": t, "abstract": a, "stored": st} for t, a, st in cur.fetchall()]
    conn.close()
    return rows


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    ap.add_argument("--out", default="bench/results/achievement-type-ab.json")
    ap.add_argument("--variants", default=None,
                    help="쉼표 구분. 예: A_현행,C_결정리스트 (표본을 키워 짝지은 검정을 할 때)")
    ap.add_argument("--base", default="A_현행",
                    help="짝지은 검정의 기준 변형. 후속 개선을 잴 때는 직전 채택안을 준다.")
    args = ap.parse_args()

    key = next(l.split("=", 1)[1].strip() for l in (REPO / ".env").read_text().splitlines()
               if l.startswith("GEMINI_API_KEY="))
    client = genai.Client(api_key=key)
    sem = asyncio.Semaphore(args.concurrency)
    papers = fetch(args.n, args.dsn)
    chosen = ([v.strip() for v in args.variants.split(",")] if args.variants
              else list(VARIANTS))
    print(f"{MODEL} · thinking={THINKING} · 논문 {len(papers)}건 · "
          f"변형 {len(chosen)}개({', '.join(chosen)}) × 2회\n")
    report = {"model": MODEL, "thinking": THINKING, "papers": len(papers), "variants": {}}
    agree_flags: dict[str, list[bool]] = {}
    first_pass: dict[str, list[str | None]] = {}
    for name in chosen:
        instruction, schema = VARIANTS[name]
        print(f"  [{name}]", flush=True)
        p1 = await pass_over(client, sem, instruction, schema, papers, "1회차")
        p2 = await pass_over(client, sem, instruction, schema, papers, "2회차")

        # 논문별 자기일치 여부 — 짝지은 검정(mcnemar)의 입력이다.
        agree_flags[name] = [
            bool(x.get("achievement_type")) and x.get("achievement_type") == y.get("achievement_type")
            for x, y in zip(p1, p2)
        ]
        first_pass[name] = [x.get("achievement_type") for x in p1]

        same, ok, conf = agreement(p1, p2)
        lo, hi = wilson(same, ok)
        dist = Counter(x.get("achievement_type") for x in p1 if x.get("achievement_type"))
        vs_stored = sum(1 for x, p in zip(p1, papers)
                        if x.get("achievement_type") and x["achievement_type"] == p["stored"])
        allowed = set(schema["properties"]["achievement_type"]["enum"])
        report["variants"][name] = {
            "자기 일치": f"{same}/{ok}",
            "자기 일치율": round(same / ok, 3) if ok else None,
            "95% 신뢰구간": [lo, hi],
            "저장된 v3와 일치": f"{vs_stored}/{len(papers)}",
            "enum 이탈": sum(1 for x in p1
                           if x.get("achievement_type") and x["achievement_type"] not in allowed),
            "실패": sum(1 for x in p1 + p2 if "_err" in x),
            "1회차 분포": dict(dist.most_common()),
            "혼동 쌍 상위": [{"쌍": f"{a} ↔ {b}", "건수": c} for (a, b), c in conf.most_common(6)],
            # 자기 자신과 어긋난 논문 — 다음 변형을 설계할 근거다. 혼동 "쌍"만 세면
            # 어느 경계가 문제인지는 알아도 왜 그런지는 알 수 없다.
            "자기불일치 표본": [
                {"제목": p["title"][:130], "1회": x.get("achievement_type"),
                 "2회": y.get("achievement_type")}
                for p, x, y in zip(papers, p1, p2)
                if x.get("achievement_type") and y.get("achievement_type")
                and x["achievement_type"] != y["achievement_type"]
            ][:40],
        }
        v = report["variants"][name]
        print(f"    → 자기 일치 {v['자기 일치']} = {v['자기 일치율']}  95%CI {lo}~{hi}\n", flush=True)

    # 짝지은 검정 — 기준 변형과 나머지를 논문 단위로 비교한다.
    base = args.base if args.base in agree_flags else chosen[0]
    if len(chosen) > 1:
        report[f"짝지은 검정(vs {base})"] = {
            name: mcnemar(agree_flags[base], agree_flags[name])
            for name in chosen if name != base
        }
        # 유형이 옮겨간 논문 — "일관성이 올랐다"가 "분류가 맞아졌다"는 아니므로
        # 사람이 눈으로 볼 표본을 남긴다.
        report[f"{base}→변형 이동 표본"] = {
            name: [
                {"제목": p["title"][:110], base: a, name: c}
                for p, a, c in zip(papers, first_pass[base], first_pass[name])
                if a and c and a != c
            ][:25]
            for name in chosen if name != base
        }

    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print(f"{'변형':<18}{'자기 일치율':>12}{'95% 신뢰구간':>20}{'저장 v3와 일치':>16}")
    print("-" * 66)
    for name, v in report["variants"].items():
        ci = f"{v['95% 신뢰구간'][0]}~{v['95% 신뢰구간'][1]}"
        print(f"{name:<18}{str(v['자기 일치율']):>12}{ci:>20}{v['저장된 v3와 일치']:>16}")
    for name, m in report.get("짝지은 검정(vs A_현행)", {}).items():
        print(f"\n짝지은 검정 A vs {name}: {m}")
    print(f"\n(전수 기준선: 19,904쌍에서 0.830)\n{args.out}")


if __name__ == "__main__":
    asyncio.run(main())
