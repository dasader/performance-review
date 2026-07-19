import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.models.analysis import Analysis
from app.models.paper import Paper, PaperExtraction
from app.prompts import MAP_INSTRUCTION, MAP_SCHEMA, map_user_text

logger = logging.getLogger(__name__)


def model_ver() -> str:
    """모델이나 thinking 설정이 바뀌면 캐시를 무효화해야 하므로 버전 문자열에 함께 넣는다."""
    return f"{settings.gemini_model}/{settings.thinking_map}"


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
    """paper_key를 요청 key로 실어 결과를 논문에 되짚을 수 있게 한다."""
    return [
        {
            "key": p.paper_key,
            "request": {
                "contents": [
                    {"role": "user",
                     "parts": [{"text": map_user_text(p.title, p.abstract)}]}
                ],
                "system_instruction": {"parts": [{"text": MAP_INSTRUCTION}]},
                "generation_config": {
                    "response_mime_type": "application/json",
                    "response_schema": MAP_SCHEMA,
                    "thinking_config": {"thinking_level": settings.thinking_map},
                },
            },
        }
        for p in papers
    ]


def chunks(requests: list[dict]) -> list[list[dict]]:
    size = settings.batch_max_requests_per_file
    return [requests[i:i + size] for i in range(0, len(requests), size)]


def estimate_tokens(papers: list[Paper]) -> int:
    """제출 전 게이트 판단용 근사치. 문자수/4 — ±20% 오차면 충분하고,
    논문마다 count_tokens를 부르면 그 호출 자체가 낭비다."""
    instruction_len = len(MAP_INSTRUCTION)
    return sum((len(p.title) + len(p.abstract) + instruction_len) // 4 for p in papers)


def save_results(db: Session, analysis: Analysis, results: list[dict]) -> int:
    """추출 결과를 저장한다. 같은 (paper_key, subfield, model_ver)는 덮어쓴다."""
    saved = 0
    for item in results:
        row = db.query(PaperExtraction).filter(
            PaperExtraction.paper_key == item["key"],
            PaperExtraction.subfield_id == analysis.subfield_id,
            PaperExtraction.model_ver == model_ver(),
        ).first()
        if row is None:
            row = PaperExtraction(
                paper_key=item["key"],
                subfield_id=analysis.subfield_id,
                model_ver=model_ver(),
            )
            db.add(row)
        row.tech_summary = item.get("tech_summary", "")
        row.achievement_type = item.get("achievement_type")
        row.metrics_json = item.get("metrics") or []
        saved += 1
    db.commit()
    return saved
