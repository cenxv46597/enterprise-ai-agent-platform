"""Application-owned answer failure categories; never serialize exception text."""

from enum import Enum

from starlette.types import Scope


class AnswerErrorCode(Enum):
    KNOWLEDGE_BASE_NOT_FOUND = ("answer_knowledge_base_not_found", 404)
    EMBEDDING_PROFILE_CONFLICT = ("answer_embedding_profile_conflict", 409)
    RETRIEVAL_CAPACITY_EXCEEDED = ("answer_retrieval_capacity_exceeded", 422)
    INPUT_OR_CONTEXT_INVALID = ("answer_input_or_context_invalid", 422)
    RERANK_INPUT_INVALID = ("answer_rerank_input_invalid", 422)
    RERANK_CONFIGURATION_INVALID = ("answer_rerank_configuration_invalid", 503)
    GENERATION_CONFIGURATION_INVALID = ("answer_generation_configuration_invalid", 503)
    EMBEDDING_TIMEOUT = ("answer_embedding_timeout", 504)
    GENERATION_TIMEOUT = ("answer_generation_timeout", 504)
    EMBEDDING_FAILED = ("answer_embedding_failed", 502)
    GENERATION_FAILED = ("answer_generation_failed", 502)
    RETRIEVAL_STATE_INVALID = ("answer_retrieval_state_invalid", 500)


def answer_error_code(scope: Scope, status_code: int | None) -> str | None:
    """Read only a typed category with its expected observed response status."""
    code = scope.get("state", {}).get("http_answer_error")
    if isinstance(code, AnswerErrorCode) and code.value[1] == status_code:
        return code.value[0]
    return None
