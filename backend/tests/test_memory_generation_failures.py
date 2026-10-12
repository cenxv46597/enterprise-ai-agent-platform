"""会话偏好调用失败与确定性算术边界；独立数据库、模型替身。"""

from app.providers.generation import GenerationTimeoutError
from test_agent_engine import ControlledGeneration
from test_conversation_api import create, payload, post
from test_conversation_execution import decisions as calculation_decisions, execution, ready
from test_memory_conversations import StyledGeneration, document, install, pref
from test_retrieval import retrieval_context


class TimeoutPreferenceGeneration(StyledGeneration):
    def generate_answer_with_preferences(self, question, sources, detail):
        self.preferences.append(detail)
        raise GenerationTimeoutError("受控偏好生成超时")


def test_unsupported_preference_capability_fails_without_plain_generation_or_snapshot(retrieval_context):
    context = retrieval_context
    document(context)
    pref(context, "concise")
    generation = ControlledGeneration()  # 只有旧 generate_answer，没有偏好能力。
    planner, _ = install(context, generation=generation)
    identifier, request = create(context), payload("如何恢复导入？")

    response = post(context, identifier, request)
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["status"] == "failed"
    assert body["run"]["stop_reason"] == "generation_error"
    assert body["run"]["steps"][-1]["error_code"] == "generation_error"
    assert body["run"]["result"] is None and body["answer_preference"] is None
    assert body["status"] == "idle" and not generation.calls
    assert len(planner.states) == 2
    assert post(context, identifier, request).json() == body
    assert not generation.calls and len(planner.states) == 2


def test_preference_generation_timeout_preserves_attempt_snapshot_and_replay(retrieval_context):
    context = retrieval_context
    document(context)
    pref(context, "concise")
    saved = pref(context, "detailed", 1)
    generation = TimeoutPreferenceGeneration()
    planner, _ = install(context, generation=generation)
    identifier, request = create(context), payload("如何恢复导入？")

    response = post(context, identifier, request)
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["status"] == "failed"
    assert body["run"]["stop_reason"] == "generation_timeout"
    assert body["run"]["steps"][-1]["error_code"] == "generation_timeout"
    assert body["run"]["result"] is None and body["status"] == "idle"
    assert body["answer_preference"] == saved.model_dump(mode="json")
    assert "answer_preference" not in body["run"]
    assert generation.preferences == ["detailed"] and not generation.calls
    # 这是进入生成调用路径的尝试，不代表模型已经收到请求或生成成功。
    pref(context, "concise", saved.version)
    assert post(context, identifier, request).json() == body
    assert generation.preferences == ["detailed"] and len(planner.states) == 2


def test_restored_sourced_calculation_does_not_apply_answer_preference(retrieval_context):
    context = retrieval_context
    identifier, reference, before, _ = ready(context)
    pref(context, "detailed")
    planner, generation = install(context, decisions=calculation_decisions(reference))

    response = post(context, identifier, execution())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready" and body["task"] == before["task"]
    assert body["run"]["status"] == "completed"
    assert [item["value"] for item in body["run"]["calculations"]] == ["90", "210"]
    assert body["answer_preference"] is None and "answer_preference" not in body["run"]
    assert "不代表原业务任务已完成" in body["reply"]
    assert not generation.preferences and not generation.calls
    assert planner.states[0]["confirmed_inputs"][0]["value"] == 3
    assert all("answer_preference" not in state["conversation_context"] for state in planner.states)
