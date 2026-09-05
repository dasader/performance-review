#!/usr/bin/env python3
"""로드맵 목표 판정 — 다중 LLM 패널(PoLL) + 자기 일치 측정.

**재는 것은 "정답률"이 아니다.** 로드맵 목표 판정에도 정답지가 없다. 이 스크립트가
답하는 것은 셋이다.

  ① 각 모델이 **자기 자신과** 일치하는가 (같은 행을 2회 판정) — 패널 탈락자를 여기서 정한다
  ② 모델들이 **서로** 일치하는가 → 만장일치 행 / 불일치 행 분리
  ③ 보고서를 쓴 모델(Gemini)의 판정이 타 계열 패널보다 관대한가 (자기 선호 편향)

③이 이 실험의 고유한 부분이다. 판정 입력 (B)인 세부기술 보고서를 Gemini가 썼으므로
Panickssery et al.(NeurIPS 2024)의 self-preference bias가 그대로 적용될 수 있다.
**그래서 Gemini는 패널 투표에서 빼고 별도 열로만 기록한다.**

## 왜 행 단위 65콜인가 (운영은 한 번에 65행 표를 만든다)

운영 경로는 `goal_count` 주입으로 축약을 막는데(65행 → 19행 실측), 모델마다 축약률이
다르면 **패널이 판정 능력이 아니라 축약 능력 차이를 재게 된다.** 행 단위로 나누면 그
교란이 사라지고 판정도 서로 독립이 된다.

## 왜 seed를 넘기지 않는가

①이 목적이기 때문이다. seed를 박으면 서빙 단 비결정성이 가려져 자기 일치가 인위적으로
1.0이 된다. 실측에서 deepseek-v4-pro는 temperature 0에서도 0.925였고(MoE 라우팅 등),
**그 바닥이 제공자마다 다르다**는 것이 알아야 할 값이다.

## 왜 제공자를 고정하는가

OpenRouter는 같은 모델을 여러 백엔드로 라우팅한다. 고정하지 않으면 "모델 간 불일치"에
"제공자 간 불일치"가 섞인다. 실제로 deepseek-v4-pro-0813은 제공자가 19곳이고 그중 8곳이
`structured_outputs`를 지원하지 않는다 — **DeepSeek 자사 엔드포인트 포함.** 고정하지
않으면 Ollama의 `format`(권고)과 같은 상태로 라우팅될 수 있다.

    # 파싱만 확인
    PYTHONPATH=backend backend/.venv/bin/python bench/roadmap_panel.py --dry-run

    # 소규모 확인 (3행 × 1회)
    PYTHONPATH=backend backend/.venv/bin/python bench/roadmap_panel.py --limit 3 --runs 1

    # 본실행
    PYTHONPATH=backend backend/.venv/bin/python bench/roadmap_panel.py
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import httpx
import psycopg2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def env(name: str, default: str | None = None) -> str:
    if name in os.environ:
        return os.environ[name]
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{name}=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip()
    if default is not None:
        return default
    raise SystemExit(f".env에 {name}이 없습니다.")


# ── 패널 구성 ────────────────────────────────────────────────────────────────
# 중급(mid-tier)만 쓴다. Kim et al.(ICML 2025) — 크고 정확한 모델일수록 오류 상관이
# 높다. PoLL(Verga et al. 2024)도 서로 다른 계열의 *작은* 모델 패널로 단일 대형
# 판정자를 이겼다. 최상위를 쌓으면 만장일치만 늘어나고 그것은 상관된 오류가 숨는 것이다.
#
# provider는 2026-09-05에 /models/{slug}/endpoints로 확인한 값이다. 고정 대상은
# structured_outputs를 지원하는 엔드포인트여야 한다.
# ⚠ 고정 제공자는 **계정의 ZDR(무보존) 정책을 통과하는 것**이어야 한다. 로드맵 원문이
# 프롬프트로 나가므로 그 설정을 켜 두었고, 그 결과 1차 제공자 여럿이 걸러진다 —
# 실측(2026-09-05): Anthropic 자사·OpenAI 자사·Alibaba 엔드포인트가 전부 ZDR로 차단됐다.
# 아래는 차단을 통과하면서 structured_outputs를 지원하는 것으로 실제 호출해 확인한 조합이다.
#
# ⚠⚠ **temperature를 받지 않는 모델이 섞여 있다.** 세 번째 항이 그 표시다.
# claude-sonnet-5와 gpt-5.4-mini는 reasoning 계열이라 OpenRouter의 supported_parameters에
# `temperature`가 없고, 보낸 값은 **에러 없이 조용히 버려진다.** 즉 그 둘의 자기 일치는
# 제공자 기본 샘플링에서 잰 값이고 temperature 0에서 잰 나머지와 **직접 비교할 수 없다**
# (자매 문서 5.6.1절 "temperature 교란과 그 해소"가 정리한 바로 그 함정).
# 깜빡한 것이 아니라 그 모델에 온도 손잡이가 없는 것이므로 통제로는 해소되지 않는다 —
# 결과에 조건을 함께 적어 두는 것이 유일한 처리다.
#
# 미통제 변수가 하나 더 있다: **여섯 모델 전부 `reasoning`을 지원하는데 사고수준을
# 지정하지 않았다.** 모델마다 기본값이 다르고, 자매 문서 5.3절이 "사고수준의 효과는
# 모델마다 방향이 반대다"를 실측해 두었다. 기준선(Gemini)만 운영값 high로 박혀 있다.
PANEL = [
    #  모델                              고정 제공자         temperature 적용?
    ("anthropic/claude-sonnet-5",        "Amazon Bedrock",   False),  # 자사 엔드포인트는 ZDR 차단
    ("openai/gpt-5.4-mini",              "Azure",            False),  # 자사 엔드포인트는 ZDR 차단
    ("deepseek/deepseek-v4-pro-0813",    "DeepInfra",        True),   # Ollama 실측과 같은 :0813 판본
    # qwen3.7-plus는 엔드포인트가 Alibaba 하나뿐이라 ZDR로 통째로 막힌다.
    # 같은 계열의 공개가중치 모델을 미국 제공자로 받는 것으로 대체한다.
    # Parasail은 실측에서 빈 응답을 돌려줬다. DeepInfra는 확인됨 — deepseek과 서빙
    # 제공자가 겹치지만 PoLL이 요구하는 것은 **모델 계열**의 분리이지 인프라 분리가 아니다.
    ("qwen/qwen3.5-397b-a17b",           "DeepInfra",        True),
    ("x-ai/grok-4.3",                     "xAI",             True),
]

# 기준선. **패널 투표에 넣지 않는다** — 판정 입력 (B)를 이 모델이 썼다.
# OpenRouter가 아니라 운영과 같은 경로(google-genai SDK + response_schema)로 부른다.
# 기준선 열의 임무는 "우리 운영 설정이 뭐라고 판정하는가"이므로 서빙 경로를 바꾸면
# 재려는 대상 자체가 바뀐다.
INCUMBENT = "gemini-3.1-flash-lite"

VERDICTS = ["관련 연구 확인", "부분 관련", "데이터 없음", "분석 범위 밖"]

# `분석 범위 밖`은 "그 중점기술의 세부기술 분석 자체를 안 돌렸다"는 뜻이라 **코드가 아는
# 사실**이다. 모델에게 물을 이유가 없는데 물었더니 잡음만 나왔다 — 실측(2026-09-05):
# 이 분야는 세부기술 10개 전부 보고서가 있어 어느 행도 해당하지 않는데 claude가 28건,
# gpt가 11건을 거기 넣었고, 이 라벨의 모델 간 Jaccard는 3모델 기준 **0.000**이었다
# (28개 행에서 누군가 썼지만 두 모델이 동시에 쓴 행이 0건).
# 그래서 보고서가 전부 있으면 이 라벨을 **열거형에서 아예 뺀다.**
def active_verdicts(all_reports_present: bool) -> list[str]:
    return VERDICTS[:3] if all_reports_present else VERDICTS

def make_schema(verdicts: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "판정": {"type": "string", "enum": verdicts},
            "근거": {"type": "string"},
        },
        "required": ["판정", "근거"],
        "additionalProperties": False,
    }


# main()이 실제 상황(세부기술 보고서 구성)을 보고 채운다.
# Google의 response_schema는 additionalProperties를 모른다 — 넣으면 400
# (`Unknown name "additional_properties"`). OpenAI 계열의 strict 모드는 반대로
# 그것을 요구한다. 스키마를 하나로 쓰지 못하는 이유다.
VERDICTS_ACTIVE: list[str] = VERDICTS
SCHEMA: dict = make_schema(VERDICTS)
SCHEMA_GEMINI: dict = {k: v for k, v in SCHEMA.items() if k != "additionalProperties"}
SYSTEM: str = ""

# 운영 ROADMAP_CHECK_INSTRUCTION의 절대 규칙 2·3·4를 그대로 옮기되, 전수 강제(규칙 1)만
# 뺐다 — 행 단위 호출이라 축약이 원리적으로 불가능하기 때문이다.
SYSTEM_TMPL = """당신은 국가전략기술 로드맵의 이행 상황을 점검하는 과학기술 분석가입니다.

입력은 두 부분입니다.
(A) 전략기술로드맵의 **기술적 목표 한 개**
(B) 해당 분야에서 실제로 발표된 논문을 분석한 세부기술별 성과 보고서

(B)의 근거만으로 (A)의 목표가 어디까지 연구되고 있는지 판정하세요.

## 절대 규칙
1. (B)에 근거가 없으면 반드시 `데이터 없음`으로 판정하세요. 추측하거나 일반 상식으로
   채우지 마세요. 근거 없는 목표가 많은 것은 정상이며, 그것을 그대로 드러내는 것이
   이 점검의 목적입니다.
2. 판정 근거로 (B)의 어느 서술을 보았는지 함께 적으세요. `데이터 없음`이면 무엇을
   찾았으나 없었는지 적으세요.
3. 논문 성과는 "연구가 진행되고 있다"는 신호일 뿐 목표 달성의 증거가 아닙니다.
   달성률 같은 숫자를 만들어내지 마세요.

## 판정은 다음 넷 중 하나만
- `관련 연구 확인` — 목표와 직접 맞닿는 연구 성과가 (B)에 있음
- `부분 관련` — 인접 주제 연구는 있으나 목표가 요구하는 수준·대상과는 어긋남
- `데이터 없음` — (B)에 근거가 없음
{범위밖}
## 출력 형식
JSON 객체 하나만 출력하세요. 설명·머리말·코드펜스를 붙이지 마세요.

{"판정": "<위 목록 중 하나를 그대로>", "근거": "<무엇을 보고 그렇게 판정했는지>"}

한국어로 답하세요."""


def build_system(report_names: list[str], all_present: bool) -> str:
    """(B)에 어떤 세부기술 보고서가 들어갔는지를 **사실로 알려준다.**

    이전에는 모델이 그것을 추측해 `분석 범위 밖`을 남발했다. 코드가 아는 것을
    코드가 말해 주는 것이 이 라벨을 없애는 것보다 우선이다.
    """
    if all_present:
        extra = (
            "\n(B)에는 이 분야의 세부기술 보고서가 **빠짐없이** 들어 있습니다"
            f" — {', '.join(report_names)} ({len(report_names)}건).\n"
            "따라서 **모든 목표가 분석 범위 안에 있습니다.** 근거를 찾지 못했다면 그것은\n"
            "분석을 안 돌려서가 아니라 근거가 없는 것이므로 `데이터 없음`입니다.\n"
        )
    else:
        extra = (
            f"\n(B)에 들어 있는 세부기술 보고서: {', '.join(report_names)}\n"
            "- `분석 범위 밖` — 해당 중점기술에 대응하는 보고서가 **위 목록에 없음**.\n"
            "  목록에 있는데 근거만 없다면 `분석 범위 밖`이 아니라 `데이터 없음`입니다.\n"
        )
    return SYSTEM_TMPL.replace("{범위밖}", extra)

# ↑ 출력 형식을 프롬프트에도 적는 이유. `response_format`만으로는 부족한 엔드포인트가
# 있다. 실측(2026-09-05): claude-sonnet-5의 ZDR 허용 엔드포인트인 Amazon Bedrock은
# json_schema를 받고도 산문을 돌려줬다(3/3 파싱 실패). 자매 문서 5.2절의 "계약 대 권고"가
# 제공자 단위로도 갈린다는 뜻이다 — 모델 카탈로그가 `structured_outputs`를 지원한다고
# 표시해도 실제 강제 여부는 엔드포인트마다 다르므로 **반드시 실호출로 확인해야 한다.**
# 이 문구는 모든 모델에 똑같이 나가므로 패널 간 공정성은 유지된다.

_SEP = re.compile(r"^\|[\s:|-]+\|$")


def parse_goals(md: str) -> list[dict]:
    """로드맵 마크다운에서 목표 행을 뽑는다.

    `reducer.count_goal_rows`와 같은 규칙(구분선 다음의 표 본문 행)으로 세되, 각 행에
    직전 제목(중점기술)을 붙여 식별자를 만든다. 개수가 count_goal_rows와 다르면
    파싱이 어긋난 것이므로 호출부에서 막는다.
    """
    goals, section, heading, in_table = [], "", "", False
    hdr: list[str] = []
    prev: list[str] = []          # 구분선 바로 앞 줄 = 머리행
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("#"):
            prev = []
            level = len(s) - len(s.lstrip("#"))
            text = s.lstrip("#").strip()
            # `## N. 이름`이 중점기술, 그 아래 ###/####가 세부 항목이다.
            # 상위를 버리면 모델이 "MRAM"만 보고 판정하게 된다.
            if level <= 2:
                section, heading = text, ""
            else:
                heading = text
            in_table = False
            continue
        if not s.startswith("|"):
            in_table = False
            prev = []
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if _SEP.match(s):
            in_table, hdr = True, prev
            continue
        if not in_table:
            prev = cells          # 머리행 후보로 들고 있는다
            continue

        # **열 위치를 가정하지 말고 머리행으로 찾는다.** 이 로드맵에는 표가 두 모양이다:
        #   | 단계 | 시기 | 기술적 목표 |   ← 시간 축이 있는 표
        #   | 구분 | 기술적 목표 |          ← 첨단패키징 등. 첫 열이 시간이 아니라 항목명
        # 위치로 읽으면 후자에서 `시기` 칸에 목표 텍스트가 중복으로 들어간다(실측 20행).
        def col(*names, default=None):
            for i, h in enumerate(hdr):
                if any(n in h for n in names):
                    return i
            return default

        i_stage = col("단계", "구분", default=0)
        i_time = col("시기")
        i_goal = col("목표", default=len(cells) - 1)
        pick = lambda i: cells[i] if i is not None and i < len(cells) else ""
        goals.append({
            "id": len(goals) + 1,
            "중점기술": section,
            "세부항목": heading or section,
            "단계": pick(i_stage),
            "시기": pick(i_time),
            # 이 행에 **시간 축이 있는가.** 머리행이 `단계`이고 값이 `N단계` 꼴일 때만 참.
            # 단계별 집계는 이 표시가 있는 행으로만 해야 한다 — 65행 중 45행뿐이고,
            # 반도체 첨단패키징 10행은 순차 단계가 아니라 병렬 항목이다.
            "단계축": bool(hdr and "단계" in hdr[i_stage]
                          and re.fullmatch(r"\d+단계", pick(i_stage))),
            "목표": pick(i_goal),
        })
    return goals


def user_text(goal: dict, context: str) -> str:
    return (
        f"# (A) 점검할 기술적 목표\n\n"
        f"- 중점기술: {goal['중점기술']}\n"
        f"- 세부 항목: {goal['세부항목']}\n"
        f"- 단계: {goal['단계']}\n"
        f"- 시기: {goal['시기']}\n"
        f"- 기술적 목표: {goal['목표']}\n\n\n"
        f"# (B) 논문 분석 기반 세부기술별 성과 보고서\n\n{context}"
    )


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse_json(text: str) -> dict:
    """코드펜스를 벗기고 파싱한다.

    structured_outputs를 지원하지 않는 엔드포인트로 새면 펜스가 붙어 온다 — 실측에서
    gemma4는 60/60 전부 감쌌지만 벗기면 가장 깨끗한 축이었다. 펜스는 눈에 보이는
    증상이고, 진짜 비용은 파싱에 성공하는 스키마 위반 쪽이다.
    """
    t = _FENCE.sub("", text.strip())
    d = json.loads(t)
    if d.get("판정") not in VERDICTS_ACTIVE:
        raise ValueError(f"enum 이탈: {d.get('판정')!r}")
    return {"판정": d["판정"], "근거": str(d.get("근거", ""))}


# ── 호출 ─────────────────────────────────────────────────────────────────────

async def call_openrouter(client, model, provider, goal, context) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_text(goal, context)},
        ],
        "temperature": 0,
        # allow_fallbacks=False가 핵심 — 지정 제공자가 막히면 조용히 다른 곳으로
        # 새는 대신 에러를 받아야 측정이 오염되지 않는다.
        "provider": {"order": [provider], "allow_fallbacks": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "verdict", "strict": True, "schema": SCHEMA},
        },
        "usage": {"include": True},
    }
    r = await client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {env('OPENROUTER_API_KEY')}"},
        json=body, timeout=180.0,
    )
    r.raise_for_status()
    d = r.json()
    if "choices" not in d:
        raise RuntimeError(str(d)[:200])
    out = parse_json(d["choices"][0]["message"]["content"])
    out["_provider"] = (d.get("provider") or provider)
    u = d.get("usage") or {}
    out["_usage"] = (u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                     float(u.get("cost", 0) or 0))
    return out


_genai_client = None


async def call_gemini(_client, model, _provider, goal, context) -> dict:
    """기준선 — 운영과 같은 경로(google-genai + response_schema)."""
    global _genai_client
    from google import genai
    from google.genai import types

    if _genai_client is None:
        _genai_client = genai.Client(api_key=env("GEMINI_API_KEY"))

    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        response_mime_type="application/json",
        response_schema=SCHEMA_GEMINI,
        temperature=0,
        # 운영 로드맵 점검이 thinking_reduce(high)로 돈다. 기준선의 임무가 "운영 설정의
        # 판정"이므로 여기만 맞춘다.
        thinking_config=types.ThinkingConfig(thinking_level=env("THINKING_REDUCE", "high")),
    )
    resp = await asyncio.to_thread(
        _genai_client.models.generate_content,
        model=model, contents=user_text(goal, context), config=cfg,
    )
    out = parse_json(resp.text)
    out["_provider"] = "Google (직접 API)"
    um = resp.usage_metadata
    out["_usage"] = (um.prompt_token_count or 0,
                     (um.candidates_token_count or 0) + (um.thoughts_token_count or 0),
                     0.0)  # 직접 API는 청구액을 응답에 주지 않는다
    return out


async def run_model(model, provider, goals, context, runs, conc, is_gemini):
    fn = call_gemini if is_gemini else call_openrouter
    sem = asyncio.Semaphore(conc)
    limits = httpx.Limits(max_connections=conc + 2)
    results, fails = [], []

    async with httpx.AsyncClient(limits=limits) as client:
        for run_i in range(runs):
            async def one(g):
                async with sem:
                    for attempt in range(3):
                        try:
                            return g["id"], await fn(client, model, provider, g, context)
                        except Exception as e:
                            if attempt == 2:
                                fails.append({"run": run_i + 1, "id": g["id"],
                                              "err": f"{type(e).__name__}: {e}"[:200]})
                                return g["id"], None
                            await asyncio.sleep(2 ** attempt * 2)

            t0 = time.monotonic()
            pairs = await asyncio.gather(*[one(g) for g in goals])
            results.append(dict(pairs))
            ok = sum(1 for v in pairs if v[1])
            print(f"    {model:<36} {run_i+1}회차 {ok}/{len(goals)}  "
                  f"{time.monotonic()-t0:.0f}s", flush=True)
    return results, fails


def agree(a: dict, b: dict) -> tuple[int, int]:
    """두 판정 묶음의 일치 수 / 양쪽 다 성공한 행 수."""
    both = [i for i in a if a.get(i) and b.get(i)]
    return sum(1 for i in both if a[i]["판정"] == b[i]["판정"]), len(both)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="반도체")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--country", default="KR")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0, help="목표 행 수 제한(연습용)")
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    ap.add_argument("--dry-run", action="store_true", help="파싱만 확인하고 종료")
    ap.add_argument("--models", default=None,
                    help="쉼표로 구분한 부분문자열. 지정한 것만 돌린다(연습용)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cur = psycopg2.connect(args.dsn).cursor()
    cur.execute("SELECT id, name FROM fields WHERE name LIKE %s", (args.field + "%",))
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"분야를 찾지 못했습니다: {args.field}")
    field_id, field_name = row

    cur.execute("SELECT content_md FROM roadmaps WHERE field_id = %s", (field_id,))
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"{field_name}에 로드맵이 없습니다.")
    roadmap_md = row[0]

    # 운영 reducer.collect_subfield_reports와 같은 조건(done + report_md 비어있지 않음).
    cur.execute("""
        SELECT s.name, a.report_md
        FROM analyses a JOIN subfields s ON s.id = a.subfield_id
        WHERE s.field_id = %s AND a.year = %s AND a.country = %s
          AND a.status = 'done' AND a.report_md IS NOT NULL AND a.report_md <> ''
        ORDER BY s.name
    """, (field_id, args.year, args.country))
    reports = cur.fetchall()
    if not reports:
        raise SystemExit(f"{field_name} {args.year}년 세부기술 보고서가 없습니다.")

    goals = parse_goals(roadmap_md)
    # 파서가 운영과 어긋나면 안 된다. 운영 count_goal_rows가 만든 값이 이미 DB에
    # 저장돼 있으므로 그것과 맞춘다 — 같은 규칙을 두 번 구현해 자기 자신과
    # 비교하는 것보다 낫다.
    cur.execute("SELECT goal_count FROM roadmap_checks WHERE field_id = %s AND year = %s",
                (field_id, args.year))
    row = cur.fetchone()
    if row and row[0] and row[0] != len(goals):
        raise SystemExit(f"파싱 불일치 — 운영 goal_count {row[0]}행 대 파서 {len(goals)}행")

    # 이 분야 세부기술 중 보고서가 없는 것이 있는지 코드가 확인한다.
    cur.execute("SELECT name FROM subfields WHERE field_id = %s ORDER BY name", (field_id,))
    all_sub = [r[0] for r in cur.fetchall()]
    have = [n for n, _ in reports]
    missing = [n for n in all_sub if n not in have]

    global VERDICTS_ACTIVE, SCHEMA, SCHEMA_GEMINI, SYSTEM
    VERDICTS_ACTIVE = active_verdicts(not missing)
    SCHEMA = make_schema(VERDICTS_ACTIVE)
    SCHEMA_GEMINI = {k: v for k, v in SCHEMA.items() if k != "additionalProperties"}
    SYSTEM = build_system(have, not missing)

    context = "\n\n".join(f"## {name}\n{md}" for name, md in reports)
    print(f"{field_name} {args.year}년 {args.country} — 목표 {len(goals)}행 × "
          f"보고서 {len(reports)}/{len(all_sub)}건 ({len(context):,}자)"
          + (f", 운영 goal_count {row[0]}행과 일치" if row and row[0] else ""))
    print(f"  판정 척도 {len(VERDICTS_ACTIVE)}지: {' / '.join(VERDICTS_ACTIVE)}"
          + (f"  (보고서 없는 세부기술: {', '.join(missing)})" if missing
             else "  ← 보고서가 전부 있어 `분석 범위 밖`을 뺐다"))

    if args.limit:
        goals = goals[:args.limit]
        print(f"  ⚠ --limit {args.limit} — 연습 실행")

    if args.dry_run:
        for g in goals[:5]:
            print(f"  [{g['id']:>2}] {g['중점기술'][:24]:<24} | {g['단계'][:8]:<8} | {g['목표'][:52]}")
        print(f"  ... 총 {len(goals)}행")
        return

    conc = args.concurrency or int(env("OPENROUTER_CONCURRENCY", "3"))
    todo = [(m, p, False) for m, p, _ in PANEL] + [(INCUMBENT, "Google", True)]
    if args.models:
        pats = [x.strip() for x in args.models.split(",") if x.strip()]
        todo = [t for t in todo if any(x in t[0] for x in pats)]

    raw, fails = {}, {}
    for model, provider, is_gem in todo:
        print(f"  {model} @ {provider}")
        raw[model], fails[model] = await run_model(
            model, provider, goals, context, args.runs, conc, is_gem)

    # ── ① 모델별 자기 일치 · 탈락률 ─────────────────────────────────────────
    per_model = {}
    for model, runs_ in raw.items():
        n = len(goals) * len(runs_)
        ok = sum(1 for r in runs_ for v in r.values() if v)
        m = {
            "제공자": next((v["_provider"] for r in runs_ for v in r.values() if v), None),
            "성공": f"{ok}/{n}", "성공률": round(ok / n, 3),
            "실패": fails[model][:10],
            "1회차 분포": dict(Counter(v["판정"] for v in runs_[0].values() if v)),
            "temperature 0 적용": dict([(m, t) for m, _, t in PANEL] +
                                       [(INCUMBENT, True)]).get(model),
            "사용량": {
                "입력 토큰": sum(v["_usage"][0] for r in runs_ for v in r.values() if v),
                "출력 토큰": sum(v["_usage"][1] for r in runs_ for v in r.values() if v),
                "청구 USD": round(sum(v["_usage"][2] for r in runs_ for v in r.values() if v), 4),
            },
        }
        if len(runs_) >= 2:
            a, b = agree(runs_[0], runs_[1])
            m["자기 일치"] = f"{a}/{b}"
            m["자기 일치율"] = round(a / b, 3) if b else None
        per_model[model] = m

    # ── ② 패널 다수결 (Gemini 제외) ─────────────────────────────────────────
    panel_models = [m for m, _, _ in PANEL if m in raw]
    votes, unanimous, disagreement = {}, [], []
    for g in goals:
        i = g["id"]
        # 자기 일치하지 않은 모델의 표는 그 행에서 버린다 — 흔들린 표를 다수결에
        # 넣으면 "모델 간 불일치"에 "그 모델의 자기 노이즈"가 섞인다.
        v = {}
        for m in panel_models:
            vals = [raw[m][r].get(i) for r in range(len(raw[m]))]
            labs = {x["판정"] for x in vals if x}
            if len(labs) == 1:
                v[m] = labs.pop()
        if not v:
            continue
        c = Counter(v.values())
        top, cnt = c.most_common(1)[0]
        rec = {"id": i, "중점기술": g["중점기술"], "단계": g["단계"], "목표": g["목표"][:80],
               "표": v, "다수결": top, "득표": f"{cnt}/{len(v)}",
               "기준선(Gemini)": (raw.get(INCUMBENT, [{}])[0].get(i) or {}).get("판정")}
        votes[i] = rec
        (unanimous if len(c) == 1 else disagreement).append(rec)

    pairwise = {}
    for x, y in combinations(panel_models, 2):
        both = [i for i in votes if x in votes[i]["표"] and y in votes[i]["표"]]
        s = sum(1 for i in both if votes[i]["표"][x] == votes[i]["표"][y])
        pairwise[f"{x.split('/')[-1]} ↔ {y.split('/')[-1]}"] = {
            "일치": f"{s}/{len(both)}", "비율": round(s / len(both), 3) if both else None}

    # ── ③ 기준선 vs 패널 (자기 선호 편향) ───────────────────────────────────
    gem = {i: (raw.get(INCUMBENT, [{}])[0].get(i) or {}).get("판정") for i in votes}
    agree_n = sum(1 for i in votes if gem[i] == votes[i]["다수결"])
    lenient = sum(1 for i in votes
                  if gem[i] == "관련 연구 확인" and votes[i]["다수결"] != "관련 연구 확인")
    strict = sum(1 for i in votes
                 if gem[i] != "관련 연구 확인" and votes[i]["다수결"] == "관련 연구 확인")
    incumbent = {
        "패널 다수결과 일치": f"{agree_n}/{len(votes)}",
        "기준선만 `관련 연구 확인` (관대)": lenient,
        "패널만 `관련 연구 확인` (엄격)": strict,
        "기준선 분포": dict(Counter(v for v in gem.values() if v)),
        "패널 다수결 분포": dict(Counter(r["다수결"] for r in votes.values())),
        "주의": "차이가 나와도 자기 선호 편향인지 하네스(직접 API vs OpenRouter) 차이인지 "
                "이 실행만으로는 가를 수 없다. 흥미로우면 Gemini를 OpenRouter로도 한 번 돌린다.",
    }

    out = {
        "분야": field_name, "연도": args.year, "국가": args.country, "목표 행": len(goals),
        "세부기술 보고서": len(reports), "실행 횟수": args.runs,
        "패널": panel_models, "기준선": INCUMBENT,
        "⚠ 자기 일치 비교 주의":
            "claude-sonnet-5·gpt-5.4-mini는 temperature를 지원하지 않아 제공자 기본 "
            "샘플링에서 측정됐다. temperature 0에서 측정된 나머지와 직접 비교할 수 없다. "
            "또한 사고수준(reasoning)은 어느 모델도 지정하지 않았다(기준선만 운영값 high).",
        "① 모델별": per_model,
        "② 패널": {"만장일치": len(unanimous), "불일치": len(disagreement),
                   "투표 성립": len(votes), "쌍별 일치": pairwise,
                   # 만장일치 행도 표를 남긴다 — 지난 실행에서 이것을 빼는 바람에
                   # 라벨별 일치도와 "몇 표짜리 만장일치인가"를 사후에 계산할 수 없었다.
                   "만장일치 행": unanimous, "불일치 행": disagreement},
        "판정 척도": VERDICTS_ACTIVE,
        "보고서 없는 세부기술": missing,
        "③ 기준선 vs 패널": incumbent,
    }
    path = Path(args.out or REPO / "bench" / "results" /
                f"roadmap-panel-{args.field}-{args.year}.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    total_usd = sum(v["사용량"]["청구 USD"] for v in per_model.values())
    out["총 청구 USD (OpenRouter)"] = round(total_usd, 4)

    print("\n── ① 자기 일치 · 탈락 ─────────────────────────")
    for m, v in per_model.items():
        print(f"  {m:<36} 성공 {v['성공']:<9} 자기일치 {str(v.get('자기 일치율', '—')):<6} "
              f"${v['사용량']['청구 USD']}")
    print("\n── ② 패널 ─────────────────────────────────────")
    print(f"  만장일치 {len(unanimous)} · 불일치 {len(disagreement)} (투표 성립 {len(votes)})")
    for k, v in pairwise.items():
        print(f"    {k:<52} {v['일치']} ({v['비율']})")
    print("\n── ③ 기준선 vs 패널 ───────────────────────────")
    print(f"  일치 {incumbent['패널 다수결과 일치']} · "
          f"기준선만 관련연구확인 {lenient} · 패널만 {strict}")
    print(f"\n총 청구 ${total_usd:.2f} (OpenRouter 기준, Gemini 제외)")
    print(f"→ {path}")


if __name__ == "__main__":
    asyncio.run(main())
