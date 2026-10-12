"""将显式偏好绑定到一次最终生成，不改问题、原文或工具上下文。"""

from collections.abc import Sequence

from app.memory.contracts import AnswerDetail
from app.providers.generation import GeneratedAnswer, GenerationError, GenerationProvider, GenerationSource


class PreferenceGenerationProvider:
    """保持旧生成接口；不支持偏好的适配器明确失败，不能静默忽略。"""

    def __init__(self, provider: GenerationProvider, answer_detail: AnswerDetail) -> None:
        if answer_detail not in ("concise", "detailed"):
            raise GenerationError("答复偏好无效")
        self._provider = provider
        self._answer_detail = answer_detail
        self._used = False

    @property
    def used(self) -> bool:
        """是否进入偏好生成调用路径；不是模型遵守程度或质量指标。"""
        return self._used

    def generate_answer(self, question: str, sources: Sequence[GenerationSource]) -> GeneratedAnswer:
        generate = getattr(self._provider, "generate_answer_with_preferences", None)
        if not callable(generate):
            raise GenerationError("当前生成适配器不支持显式答复偏好")
        self._used = True
        return generate(question, sources, self._answer_detail)
