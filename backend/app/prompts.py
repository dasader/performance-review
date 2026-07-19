MAP_INSTRUCTION = """당신은 한국 연구성과를 분석하는 과학기술 분석가입니다.
논문 한 편의 제목과 초록만 보고, 그 논문이 달성한 **기술적 성과**를 정리하세요.

규칙:
- 초록에 명시된 내용만 사용하고 추측하지 마세요.
- tech_summary: 무엇을 어떻게 달성했는지 1~2문장. 연구 동기나 배경은 빼고 성과만.
- achievement_type: 신소자, 신소재, 공정, 알고리즘, 아키텍처, 성능향상, 시스템구현, 이론/해석, 기타 중 하나.
- metrics: 초록에 수치가 있을 때만 채우고, 없으면 빈 배열.
- 한국어로 작성하세요."""

MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "tech_summary": {"type": "string"},
        "achievement_type": {"type": "string"},
        "metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                },
                "required": ["name", "value"],
            },
        },
    },
    "required": ["tech_summary", "achievement_type", "metrics"],
}


def map_user_text(title: str, abstract: str) -> str:
    return f"제목: {title}\n\n초록: {abstract}"
