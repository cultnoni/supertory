"""첨삭 파이프라인 상수."""

from __future__ import annotations

FEEDBACK_MODEL = "claude-sonnet-5"
HAIKU_45_MODEL = "claude-haiku-4-5-20251001"
PROMPT_VERSION = "pipeline-v0.5"
LITERATURE_PROMPT_VERSION = "literature-short-v0.1"
LITERATURE_LONG_PROMPT_VERSION = "literature-long-v0.1"
LITERATURE_MIDCHECK_PROMPT_VERSION = "literature-midcheck-v0.3"
# 단계마다 모델을 바꿀 수 있다. 기본은 Sonnet 5.
# 문체 추출(style)과 의미 검증(verify)만 비교할 때는 측정 옵션으로
# stage_models={"style": HAIKU_45_MODEL, "verify": HAIKU_45_MODEL} 를 넘긴다.
LITERATURE_STAGE_MODELS = {
    "style": FEEDBACK_MODEL,
    "scene_map": FEEDBACK_MODEL,
    "rubric": FEEDBACK_MODEL,
    "report": FEEDBACK_MODEL,
    "verify": FEEDBACK_MODEL,
    "cards": FEEDBACK_MODEL,
}
# 장편 이전 단위 요약 압축만 Haiku. 캐시 앞부분 만들기 전 별도 호출이라 Sonnet 캐시와 공유되지 않는다.
LITERATURE_LONG_STAGE_MODELS = {
    **LITERATURE_STAGE_MODELS,
    "compress": HAIKU_45_MODEL,
}
LITERARY_RECENT_UNITS = 5
LITERARY_COMPRESS_GROUP = 5
LITERARY_STYLE_LOOKBACK = 3
MIDCHECK_VERIFY_LIMIT = 5
MIDCHECK_NUDGE_EVERY = 10
LONG_QUOTE_MONOLOGUE_CHARS = 300
# 거절 기반 의도적 선택 제안: 서로 다른 실행 MIN_RUNS회 이상에 걸쳐 MIN_REJECTS번 이상 ignored.
INTENT_SUGGEST_MIN_REJECTS = 3
INTENT_SUGGEST_MIN_RUNS = 2
INTENT_SUGGEST_PIPELINES = frozenset({"literature_short", "literature_long"})

CARD_CONCURRENCY = 3
MAX_CARDS_PER_RUN = 100
MAX_COST_USD_PER_RUN = 2.0
MAX_COST_USD_MIDCHECK = 6.0

# correction·consistency 카드는 항상 수정안을 켠다.
SUGGESTION_ENABLED_TYPES = frozenset(
    {
        "explain_less",
        "redundancy",
        "action_clarity",
        "reader_question",
        "abstract_expression",
    }
)
SUGGESTION_DISABLED_TYPES = frozenset({"info_placement", "other"})
