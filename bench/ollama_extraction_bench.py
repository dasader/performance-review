#!/usr/bin/env python3
"""Ollama Cloud를 추출(map) 단계 대안으로 재는 벤치마크.

왜 이런 모양인가 — 재야 할 것이 셋이고 서로 독립적이다.

1. **처리량**: 추출은 20만 건 규모라 "한 건이 몇 초냐"가 아니라 "초당 몇 토큰이냐"가
   전부다. 총 출력 토큰을 처리량으로 나눈 값이 곧 완료 시간이다.
2. **Gemini와 같은 결과가 나오는가**: 단, Gemini도 비결정적이라 100% 일치는 애초에
   불가능하다. 그래서 **Gemini가 같은 논문을 두 번 뽑았을 때의 자기 일치율**을
   기준선으로 둔다(실측 2026-08-25, 중복 19,904그룹: achievement_type 81.9%,
   metrics 52.2%, tech_summary 0.1%). 그 아래로 크게 떨어지지 않으면 "모델을 바꿔서
   생긴 차이"가 아니라 원래 있던 샘플링 잡음이다.
3. **사고수준을 줄여도 되는가**: think="low"와 think=false를 **같은 논문**에 돌려
   서로 비교한다. 둘의 일치율이 기준선 수준이면 사고는 값을 더하지 않는 것이다.

입력은 실제 DB의 논문이고, 프롬프트는 운영과 완전히 같은 것을 쓴다
(app.prompts.MAP_INSTRUCTION / map_user_text / MAP_SCHEMA) — 프롬프트를 새로 쓰면
무엇을 비교하는지 알 수 없게 된다.

실행:
    PYTHONPATH=backend backend/.venv/bin/python bench/ollama_extraction_bench.py --n 40
"""
import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

import httpx
import psycopg2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from app.prompts import MAP_INSTRUCTION, MAP_SCHEMA, map_user_text  # noqa: E402

ENDPOINT = "https://ollama.com/api/chat"


def env(name: str, default: str | None = None) -> str:
    """.env에서 읽는다 — 벤치마크는 컨테이너 밖에서 돌고 키는 .env에만 있다."""
    if name in os.environ:
        return os.environ[name]
    for line in (REPO / ".env").read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    if default is None:
        raise SystemExit(f".env에 {name}이 없습니다.")
    return default


# ── 비교 지표 ────────────────────────────────────────────────────────────────

def _s(v) -> str:
    """MAP_SCHEMA는 metrics[].value를 string으로 선언하지만 모델이 지키지 않는 일이
    있다(실측: Ollama/deepseek-v4-flash가 숫자를 그대로 낸다). 비교기가 거기서 죽지
    않게 문자열로 맞춘다 — 위반 자체는 _type_violations가 따로 센다."""
    if v is None:
        return ""
    return str(v).strip()


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def strip_fence(content: str) -> tuple[str, bool]:
    """```json ... ``` 껍데기를 벗긴다. 두 번째 값은 껍데기가 있었는지.

    Ollama의 `format`(JSON 스키마)은 Gemini의 response_schema와 달리 **강제되지
    않는다** — 실측으로 think=false일 때 모델이 코드펜스로 감싸 돌려준다. 실무에서는
    이렇게 벗겨 쓰면 되지만, 벗겨야 한다는 사실 자체가 "구조화 출력이 보장되지
    않는다"는 뜻이라 별도로 센다.
    """
    m = _FENCE.match(content or "")
    return (m.group(1), True) if m else (content, False)


def _blank_metric_rows(rec: dict) -> int:
    """이름이 빈 지표 원소 수. 초록에 수치가 없으면 metrics는 빈 배열이어야 하는데
    (MAP_INSTRUCTION 명시) 가짜 원소를 채워 넣으면 stats가 '수치 있음'으로 센다."""
    return sum(1 for m in (rec.get("metrics") or [])
               if isinstance(m, dict) and not _s(m.get("name")))


def _type_violations(rec: dict) -> int:
    """metrics[].value/name/unit이 스키마 선언(string)과 다른 타입으로 온 횟수."""
    n = 0
    for m in (rec.get("metrics") or []):
        if not isinstance(m, dict):
            n += 1
            continue
        n += sum(1 for k in ("name", "value", "unit", "target")
                 if k in m and m[k] is not None and not isinstance(m[k], str))
    return n


def _norm_metric(m: dict) -> tuple:
    """지표를 비교 가능한 형태로. 이름 표기 흔들림(공백·괄호)을 걷어낸다."""
    name = re.sub(r"[\s()]+", "", _s(m.get("name"))).lower()
    value = _s(m.get("value"))
    unit = re.sub(r"\s+", "", _s(m.get("unit"))).lower()
    return (name, value, unit)


def _metric_set(rec: dict) -> set:
    return {_norm_metric(m) for m in (rec.get("metrics") or []) if isinstance(m, dict)}


def _korean_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if "가" <= c <= "힣") / len(text)


def _bigrams(s: str) -> set:
    s = re.sub(r"\s+", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _similarity(a: str, b: str) -> float:
    """문자 바이그램 자카드. 임베딩 없이 '같은 얘기를 하는가'의 거친 대리 지표다 —
    정밀하지 않지만 의존성 없이 수백 쌍을 재기에는 충분하고, 최종 판단은 사람이
    샘플을 눈으로 보고 한다(리포트에 원문을 남기는 이유)."""
    A, B = _bigrams(a), _bigrams(b)
    return len(A & B) / len(A | B) if A | B else 0.0


def compare(x: dict | None, y: dict | None) -> dict | None:
    if not x or not y:
        return None
    mx, my = _metric_set(x), _metric_set(y)
    return {
        "type_match": x.get("achievement_type") == y.get("achievement_type"),
        "metrics_presence_match": bool(mx) == bool(my),
        "metrics_jaccard": len(mx & my) / len(mx | my) if (mx | my) else 1.0,
        "summary_sim": _similarity(x.get("tech_summary", ""), y.get("tech_summary", "")),
    }


# ── 호출 ─────────────────────────────────────────────────────────────────────

async def call(client, model, title, abstract, think, sem, temperature=None):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": MAP_INSTRUCTION},
            {"role": "user", "content": map_user_text(title, abstract)},
        ],
        "stream": False,
        "format": MAP_SCHEMA,
        "think": think,
    }
    # 기본값 None이면 아무것도 싣지 않는다 — 제공자 기본 temperature 그대로.
    # 자기 일치는 "같은 입력에 같은 답이 나올 확률"이라 temperature가 직접 그 값을
    # 움직인다. 제공자마다 기본값이 다르면(Gemini 1.0, Ollama 서버 0.8) 모델 간
    # 비교가 그 차이에 오염되므로, 비교할 때는 여기에 같은 값을 박아 넣는다.
    if temperature is not None:
        body["options"] = {"temperature": temperature}
    async with sem:
        t0 = time.monotonic()
        try:
            r = await client.post(ENDPOINT, json=body)
            wall = time.monotonic() - t0
            if r.status_code != 200:
                return {"ok": False, "err": f"HTTP {r.status_code}: {r.text[:120]}", "wall": wall}
            d = r.json()
        except Exception as e:  # 네트워크·타임아웃도 실패율에 그대로 센다
            return {"ok": False, "err": f"{type(e).__name__}: {e}", "wall": time.monotonic() - t0}

    msg = d.get("message", {}) or {}
    content = msg.get("content") or ""
    thinking = msg.get("thinking") or ""
    body_text, fenced = strip_fence(content)
    try:
        parsed = json.loads(body_text)
        schema_ok = isinstance(parsed, dict) and all(k in parsed for k in MAP_SCHEMA["required"])
    except Exception:
        parsed, schema_ok = None, False
    return {
        "ok": True,
        "wall": wall,
        "in_tok": d.get("prompt_eval_count") or 0,
        "out_tok": d.get("eval_count") or 0,          # thinking 포함(생성 토큰 전체)
        "think_chars": len(thinking),
        "fenced": fenced,
        "raw_parse_ok": not fenced and schema_ok,     # 껍데기 없이 곧바로 JSON이었나
        "schema_ok": schema_ok,
        "parsed": parsed,
    }


async def run_condition(papers, model, think, concurrency, label, temperature=None):
    sem = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency + 2)
    headers = {"Authorization": f"Bearer {env('OLLAMA_API_KEY')}"}
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=300.0, headers=headers, limits=limits) as client:
        results = await asyncio.gather(*[
            call(client, model, p["title"], p["abstract"], think, sem, temperature)
            for p in papers
        ])
    elapsed = time.monotonic() - t0
    print(f"  [{label}] {len(papers)}건 완료 — {elapsed:.1f}초", flush=True)
    return results, elapsed


# ── 본체 ─────────────────────────────────────────────────────────────────────

def fetch_papers(n: int, dsn: str) -> list[dict]:
    """Gemini v3 추출이 이미 있는 논문만 뽑는다 — 비교 대상이 있어야 한다."""
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("""
        SELECT p.paper_key, p.title, p.abstract,
               e.tech_summary, e.achievement_type, e.approach, e.improvement, e.metrics_json
        FROM papers p
        JOIN paper_extractions e ON e.paper_key = p.paper_key
        WHERE p.abstract <> '' AND e.model_ver LIKE %s
        ORDER BY md5(p.paper_key)   -- 결정론적 무작위(재실행 시 같은 표본)
        LIMIT %s
    """, ("%v3", n))
    rows = []
    for k, t, a, ts, at, ap, im, mj in cur.fetchall():
        rows.append({
            "paper_key": k, "title": t, "abstract": a,
            "gemini": {"tech_summary": ts, "achievement_type": at, "approach": ap,
                       "improvement": im, "metrics": mj or []},
        })
    conn.close()
    return rows


def summarize(results, elapsed, papers, other=None):
    ok = [r for r in results if r["ok"]]
    parsed_ok = [r for r in ok if r["schema_ok"]]
    out_tok = sum(r["out_tok"] for r in ok)
    s = {
        "요청": len(results),
        "성공": len(ok),
        "스키마 준수": len(parsed_ok),
        "총 소요(초)": round(elapsed, 1),
        "출력토큰 합": out_tok,
        "처리량(tok/s)": round(out_tok / elapsed, 1) if elapsed else 0,
        "논문/분": round(len(ok) / elapsed * 60, 1) if elapsed else 0,
        "건당 지연 중앙값(초)": round(statistics.median([r["wall"] for r in ok]), 1) if ok else 0,
        "건당 입력토큰 평균": round(statistics.mean([r["in_tok"] for r in ok])) if ok else 0,
        "건당 출력토큰 평균": round(statistics.mean([r["out_tok"] for r in ok])) if ok else 0,
        "thinking 문자 평균": round(statistics.mean([r["think_chars"] for r in ok])) if ok else 0,
    }
    s["코드펜스로 감쌈"] = f"{sum(1 for r in ok if r['fenced'])}/{len(ok)}"
    s["껍데기 없이 순수 JSON"] = f"{sum(1 for r in ok if r['raw_parse_ok'])}/{len(ok)}"

    kor, cmps, viol, viol_rows, enum_bad, blank_rows = [], [], 0, 0, 0, 0
    allowed = set(MAP_SCHEMA["properties"]["achievement_type"]["enum"])
    for r, p in zip(results, papers):
        if not (r["ok"] and r["schema_ok"]):
            continue
        kor.append(_korean_ratio(_s(r["parsed"].get("tech_summary"))))
        v = _type_violations(r["parsed"])
        viol += v
        viol_rows += 1 if v else 0
        blank_rows += 1 if _blank_metric_rows(r["parsed"]) else 0
        if r["parsed"].get("achievement_type") not in allowed:
            enum_bad += 1
        c = compare(r["parsed"], p["gemini"])
        if c:
            cmps.append(c)
    if kor:
        s["한국어 비율 평균"] = round(statistics.mean(kor), 3)
    # 파싱은 됐지만 선언한 타입/enum을 지키지 않은 응답 — 파이프라인에 그대로 넣으면
    # stats._metric_value·group_for_reduce가 조용히 다르게 동작한다.
    s["metrics 타입위반 논문"] = f"{viol_rows}/{len(parsed_ok)}"
    s["metrics 타입위반 필드수"] = viol
    s["빈 이름 지표를 채운 논문"] = f"{blank_rows}/{len(parsed_ok)}"
    s["achievement_type enum 이탈"] = f"{enum_bad}/{len(parsed_ok)}"
    if cmps:
        s["vs Gemini 성과유형 일치"] = f"{sum(c['type_match'] for c in cmps)}/{len(cmps)}"
        s["vs Gemini 수치유무 일치"] = f"{sum(c['metrics_presence_match'] for c in cmps)}/{len(cmps)}"
        s["vs Gemini 수치 자카드"] = round(statistics.mean(c["metrics_jaccard"] for c in cmps), 3)
        s["vs Gemini 요약 유사도"] = round(statistics.mean(c["summary_sim"] for c in cmps), 3)
    if other:
        pair = [compare(a["parsed"], b["parsed"])
                for a, b in zip(results, other)
                if a["ok"] and a["schema_ok"] and b["ok"] and b["schema_ok"]]
        pair = [c for c in pair if c]
        if pair:
            s["low↔none 성과유형 일치"] = f"{sum(c['type_match'] for c in pair)}/{len(pair)}"
            s["low↔none 수치 자카드"] = round(statistics.mean(c["metrics_jaccard"] for c in pair), 3)
            s["low↔none 요약 유사도"] = round(statistics.mean(c["summary_sim"] for c in pair), 3)
    return s


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--dsn", default="postgresql://perfrev:perfrev@localhost:5403/perfrev")
    ap.add_argument("--out", default="bench/results/ollama-latest.json")
    # 모델을 .env가 아니라 인자로 받는다 — 여러 모델을 비교하려면 .env를 고쳐가며
    # 돌릴 이유가 없고, 그러다 보면 어느 결과가 어느 모델이었는지 잃는다.
    ap.add_argument("--model", default=None, help="기본값은 .env의 OLLAMA_MODEL")
    ap.add_argument("--concurrency", type=int, default=None,
                    help="기본값은 .env의 OLLAMA_CONCURRENCY. 계정 한도를 같이 쓰는 "
                         "다른 서비스가 있으므로 올릴 때는 그쪽 몫을 남길 것")
    args = ap.parse_args()

    model = args.model or env("OLLAMA_MODEL")
    conc = args.concurrency or int(env("OLLAMA_CONCURRENCY", "2"))
    papers = fetch_papers(args.n, args.dsn)
    print(f"모델 {model} · 동시 {conc} · 논문 {len(papers)}건\n", flush=True)

    # think=false를 먼저 돌린다 — 어느 쪽을 먼저 돌리든 결과가 같아야 정상이고,
    # 순서가 워밍업으로 유리해지는 쪽을 저사고 조건에 주지 않기 위해서다.
    none_res, none_t = await run_condition(papers, model, False, conc, "think=none")
    low_res, low_t = await run_condition(papers, model, "low", conc, "think=low")

    report = {
        "model": model, "concurrency": conc, "papers": len(papers),
        "think_none": summarize(none_res, none_t, papers, other=low_res),
        "think_low": summarize(low_res, low_t, papers, other=none_res),
        "errors": [r["err"] for r in none_res + low_res if not r["ok"]][:10],
        "samples": [
            {
                "title": p["title"][:120],
                "gemini": p["gemini"]["tech_summary"],
                "ollama_low": (lr["parsed"] or {}).get("tech_summary") if lr["ok"] else lr.get("err"),
                "ollama_none": (nr["parsed"] or {}).get("tech_summary") if nr["ok"] else nr.get("err"),
                "types": [p["gemini"]["achievement_type"],
                          (lr["parsed"] or {}).get("achievement_type") if lr["ok"] else None,
                          (nr["parsed"] or {}).get("achievement_type") if nr["ok"] else None],
            }
            for p, lr, nr in list(zip(papers, low_res, none_res))[:8]
        ],
    }
    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print()
    keys = sorted(set(report["think_none"]) | set(report["think_low"]))
    print(f"{'지표':<26}{'think=none':>14}{'think=low':>14}")
    print("-" * 54)
    for k in keys:
        print(f"{k:<26}{str(report['think_none'].get(k, '-')):>14}{str(report['think_low'].get(k, '-')):>14}")
    if report["errors"]:
        print("\n실패 예시:", report["errors"][:3])
    print(f"\n전체 결과: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
