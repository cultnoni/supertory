"""첨삭 파이프라인 상수."""

from __future__ import annotations

FEEDBACK_MODEL = "claude-sonnet-5"
PROMPT_VERSION = "pipeline-v0.5"
CARD_CONCURRENCY = 3
MAX_CARDS_PER_RUN = 100
MAX_COST_USD_PER_RUN = 2.0

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
