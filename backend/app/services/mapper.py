import logging

from google.genai import types
from sqlalchemy.orm import Session

from app.config import settings
from app.models.analysis import Analysis
from app.models.paper import Paper, PaperExtraction
from app.prompts import MAP_INSTRUCTION, MAP_SCHEMA, map_user_text

logger = logging.getLogger(__name__)

# MAP_SCHEMA(prompts.py)에 필드를 추가/변경할 때마다 이 값을 올릴 것 — model_ver에
# 섞여 들어가 기존 추출 결과를 자동으로 재추출 대상(superseded)으로 만든다. 기존
# 행은 지우지 않는다(pending_papers가 새 model_ver로만 조회하므로 자연히 무시된다).
EXTRACTION_SCHEMA_VERSION = 3


def model_ver() -> str:
    """모델·thinking 설정·추출 스키마 버전이 바뀌면 캐시를 무효화해야 하므로
    버전 문자열에 함께 넣는다."""
    return f"{settings.gemini_model}/{settings.thinking_map}/v{EXTRACTION_SCHEMA_VERSION}"


def pending_papers(db: Session, analysis: Analysis, papers: list[Paper]) -> list[Paper]:
    """abstract가 있고 아직 이 세부기술로 추출되지 않은 논문만 남긴다."""
    with_abstract = [p for p in papers if p.abstract]
    if not with_abstract:
        return []

    keys = [p.paper_key for p in with_abstract]
    cached = {
        row.paper_key
        for row in db.query(PaperExtraction.paper_key).filter(
            PaperExtraction.paper_key.in_(keys),
            PaperExtraction.subfield_id == analysis.subfield_id,
            PaperExtraction.model_ver == model_ver(),
        )
    }
    pending = [p for p in with_abstract if p.paper_key not in cached]
    logger.info(
        "[map] 대상 %d건 (abstract 보유 %d / 캐시 히트 %d)",
        len(pending), len(with_abstract), len(cached),
    )
    return pending


def build_requests(papers: list[Paper]) -> list[dict]:
    """paper_key를 요청 key로 실어 결과를 논문에 되짚을 수 있게 한다.

    request 본문은 손으로 만든 dict가 아니라 google-genai SDK 타입(Content/Part/
    GenerateContentConfig)으로 만들고 model_dump(by_alias=True, mode="json")으로
    직렬화한다. 와이어 스키마는 camelCase이고, systemInstruction은 request 최상위
    (contents/generationConfig와 형제)에, responseMimeType/responseSchema/
    thinkingConfig는 generationConfig 안에 중첩된다 — 이 구조는 SDK 내부 변환 로직
    (google/genai/batches.py::_InlinedRequest_to_mldev,
    google/genai/models.py::_GenerateContentConfig_to_mldev)의 실제 소스로 확인했다.
    GenerateContentConfig를 통째로 dump하면 systemInstruction이 잘못된 위치(다른
    generationConfig 필드들과 같은 레벨)에 나오므로, system instruction은 별도
    Content로 분리해 조립한다.
    """
    return [
        {
            "key": p.paper_key,
            "request": {
                "contents": [
                    types.Content(
                        role="user",
                        parts=[types.Part(text=map_user_text(p.title, p.abstract))],
                    ).model_dump(by_alias=True, exclude_none=True, mode="json")
                ],
                "systemInstruction": types.Content(
                    parts=[types.Part(text=MAP_INSTRUCTION)]
                ).model_dump(by_alias=True, exclude_none=True, mode="json"),
                "generationConfig": types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=MAP_SCHEMA,
                    thinking_config=types.ThinkingConfig(thinking_level=settings.thinking_map),
                ).model_dump(by_alias=True, exclude_none=True, mode="json"),
            },
        }
        for p in papers
    ]


def chunks(requests: list[dict]) -> list[list[dict]]:
    size = settings.batch_max_requests_per_file
    return [requests[i:i + size] for i in range(0, len(requests), size)]


def _estimate_text_tokens(text: str) -> int:
    """근사치: ASCII는 4자/토큰(영어 경험칙), 비ASCII(한글 등)는 2자/토큰으로
    따로 센다. 문자수/4를 그대로 쓰면 한국어 비중이 큰 텍스트에서 토큰 수를
    과소평가한다."""
    ascii_len = sum(1 for c in text if ord(c) < 128)
    non_ascii_len = len(text) - ascii_len
    return ascii_len // 4 + non_ascii_len // 2


# MAP_INSTRUCTION은 상수라 토큰 수도 상수다. token_capped_chunk가 논문마다
# estimate_tokens([paper])를 부르므로, 캐시하지 않으면 1.5KB 프롬프트를 논문 수만큼
# 문자 단위로 다시 훑는다(1,000건 제출 시 150만 회 규모).
_INSTRUCTION_TOKENS = _estimate_text_tokens(MAP_INSTRUCTION)


def estimate_tokens(papers: list[Paper]) -> int:
    """제출 전 게이트 판단용 근사치 — ±20% 오차면 충분하고, 논문마다
    count_tokens를 부르면 그 호출 자체가 낭비다."""
    return sum(
        _INSTRUCTION_TOKENS + _estimate_text_tokens(p.title) + _estimate_text_tokens(p.abstract)
        for p in papers
    )


def estimate_llm_cost_usd(paper_count: int) -> float:
    """미리보기용 map 단계 LLM 비용 상한선 추정(C3).

    실제 논문의 title/abstract는 미리보기 시점엔 알 수 없으므로 논문당 평균 토큰
    설정값(settings.gemini_avg_*_tokens_per_paper)으로 대신한다. instruction 토큰만은
    실제 MAP_INSTRUCTION 텍스트로 계산한다(estimate_tokens와 같은 근사식 재사용).
    """
    input_tokens = paper_count * (_INSTRUCTION_TOKENS + settings.gemini_avg_input_tokens_per_paper)
    output_tokens = paper_count * settings.gemini_avg_output_tokens_per_paper
    return (
        input_tokens / 1_000_000 * settings.gemini_batch_input_usd_per_1m
        + output_tokens / 1_000_000 * settings.gemini_batch_output_usd_per_1m
    )


def token_capped_chunk(papers: list[Paper], requests: list[dict]) -> list[dict]:
    """제출 직전 청크(이미 batch_max_requests_per_file건 이하로 잘려 있음)를
    settings.batch_max_enqueued_tokens도 넘지 않도록 앞에서부터 다시 자른다(C2).

    papers와 requests는 같은 순서로 1:1 대응한다(build_requests가 그렇게 만든다).
    최소 1건은 항상 담아, 논문 한 편이 토큰 상한을 혼자 넘어도 무한 대기에
    빠지지 않게 한다. 잘리고 남은 나머지는 다음 루프 틱에서 이어 제출된다.
    """
    capped: list[dict] = []
    tokens = 0
    for paper, req in zip(papers, requests):
        paper_tokens = estimate_tokens([paper])
        if capped and tokens + paper_tokens > settings.batch_max_enqueued_tokens:
            break
        capped.append(req)
        tokens += paper_tokens
    return capped


def save_results(db: Session, analysis: Analysis, results: list[dict]) -> int:
    """추출 결과를 저장한다. 같은 (paper_key, subfield, model_ver)는 덮어쓴다."""
    # 결과 1건마다 SELECT를 날리면 703건 배치에서 703번 질의가 폴링 루프 한 틱 안에
    # 몰린다. 기존 행을 한 번에 읽어 사전으로 들고 쓴다. model_ver()도 행마다
    # 문자열을 다시 만들 이유가 없어 루프 밖으로 뺀다.
    ver = model_ver()
    existing = {
        row.paper_key: row
        for row in db.query(PaperExtraction).filter(
            PaperExtraction.paper_key.in_([item["key"] for item in results]),
            PaperExtraction.subfield_id == analysis.subfield_id,
            PaperExtraction.model_ver == ver,
        )
    } if results else {}

    saved = 0
    for item in results:
        row = existing.get(item["key"])
        if row is None:
            row = PaperExtraction(
                paper_key=item["key"],
                subfield_id=analysis.subfield_id,
                model_ver=ver,
            )
            db.add(row)
            # 사전에도 넣어둔다 — 같은 배치에 같은 key가 두 번 오면 두 번째는 이 행을
            # 다시 찾아 덮어쓴다. 넣지 않으면 둘 다 신규로 보고 중복 행을 만든다.
            existing[item["key"]] = row
            # 비용이 발생한 건수(analysis.extracted_this_run → AnalysisRun.new_papers)라
            # 신규 행일 때만 센다. 같은 key가 두 번 와도 LLM 호출은 한 번이었다.
            saved += 1
        row.tech_summary = item.get("tech_summary", "")
        row.achievement_type = item.get("achievement_type")
        row.metrics_json = item.get("metrics") or []
        row.approach = item.get("approach") or ""
        row.improvement = item.get("improvement") or ""
    db.commit()
    return saved
