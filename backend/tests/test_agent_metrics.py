"""计数在实际调用边界记录；用受控时钟验证耗时，不依赖真实延迟。"""

from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from app.agents import engine
from app.agents.contracts import AgentRun, FinishAction, PlannerTimeoutError, ToolAction, ToolExecutionError
from app.agents.engine import run_agent
from app.agents.tools import ToolRegistry
from app.api.agent_runs import AgentRunRead
from app.providers.generation import GenerationError
from test_agent_engine import ControlledGeneration, ControlledPlanner, ControlledTool, hit, search, setup
from test_document_candidates_observation import find_documents
from test_document_candidates_tool import candidate_result
from test_document_status_agent import status_flow  # noqa: F401 -- 复用纯替身fixture
from test_document_status_tool import registered_status_tool  # noqa: F401 -- status_flow的依赖


@pytest.mark.parametrize("rejected", [
    ToolAction(action="tool", tool_name="knowledge_search", arguments={"query": True}),
    ToolAction(action="tool", tool_name="knowledge_search", arguments={"query": "问题", "workspace_id": "其他空间"}),
    ToolAction(action="tool", tool_name="unknown", arguments={}),
])
def test_rejected_attempt_then_valid_call_counts_only_one_execution(rejected):
    registry, tool = setup([[hit()]])
    planner = ControlledPlanner([rejected, search(), FinishAction(action="finish")])
    run = run_agent(planner, ControlledGeneration(), registry, "问题")
    assert run.status == "completed" and run.metrics.decision_count == 3
    assert run.metrics.tool_execution_count == len(tool.calls) == 1
    assert [step.action for step in run.steps] == ["tool", "tool", "finish"]
    assert run.steps[0].status == "error" and run.metrics.total_ms >= 0
    assert all("metrics" not in state for state in planner.states)


def test_tool_timeout_still_counts_as_execution():
    registry, tool = setup([ToolExecutionError("tool_timeout")])
    run = run_agent(ControlledPlanner([search(), FinishAction(action="finish")]), ControlledGeneration(), registry, "问题")
    assert run.status == "failed" and run.metrics.decision_count == 2
    assert run.metrics.tool_execution_count == len(tool.calls) == 1


@pytest.mark.parametrize("decisions,max_steps,executions,reason", [
    ([search(), search()], 4, 1, "duplicate_action"),
    ([search()], 1, 0, "step_limit"),
])
def test_stopped_actions_do_not_count_unexecuted_tools(decisions, max_steps, executions, reason):
    registry, tool = setup([[hit()]])
    run = run_agent(ControlledPlanner(decisions), ControlledGeneration(), registry, "问题", max_steps=max_steps)
    assert run.status == "stopped" and run.stop_reason == reason
    assert run.metrics.decision_count == len(decisions)
    assert run.metrics.tool_execution_count == len(tool.calls) == executions


def test_failed_planning_preserves_metrics_for_calls_already_made():
    registry, tool = setup([[hit()]])
    run = run_agent(ControlledPlanner([search(), PlannerTimeoutError("private")]), ControlledGeneration(), registry, "问题")
    assert run.status == "failed" and run.stop_reason == "planner_timeout"
    assert run.metrics.decision_count == 2 and run.metrics.tool_execution_count == len(tool.calls) == 1


def test_generation_failure_keeps_runtime_summary():
    registry, _ = setup([[hit()]])
    run = run_agent(ControlledPlanner([search(), FinishAction(action="finish")]),
                    ControlledGeneration(error=GenerationError("private")), registry, "问题")
    assert run.status == "failed" and run.stop_reason == "generation_error"
    assert run.metrics.decision_count == 2 and run.metrics.tool_execution_count == 1


def test_total_time_covers_decision_tool_and_final_generation(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(engine, "monotonic", lambda: clock[0])

    class TimedPlanner(ControlledPlanner):
        def decide(self, state):
            clock[0] += 0.025
            return super().decide(state)

    class TimedTool(ControlledTool):
        def execute(self, arguments, context=None):
            clock[0] += 0.075
            return super().execute(arguments, context)

    class TimedGeneration(ControlledGeneration):
        def generate_answer(self, question, sources):
            clock[0] += 0.040
            return super().generate_answer(question, sources)

    registry = ToolRegistry()
    registry.register(TimedTool([[hit()]]))
    run = run_agent(TimedPlanner([search(), FinishAction(action="finish")]), TimedGeneration(), registry, "问题")
    assert run.metrics.total_ms == pytest.approx(165.0)
    assert sum(step.planner_ms for step in run.steps) == pytest.approx(50.0)
    assert run.steps[0].execution_ms == pytest.approx(75.0) and run.steps[1].execution_ms == pytest.approx(40.0)


def test_waiting_for_document_choice_has_current_run_metrics(status_flow):
    status_flow.candidates = candidate_result(2)
    run = run_agent(ControlledPlanner([find_documents()]), ControlledGeneration(), status_flow.registry,
                    "文件状态", response_mode="document_status")
    assert run.status == "waiting_for_user" and run.metrics.decision_count == 1
    assert run.metrics.tool_execution_count == 1 and run.metrics.total_ms >= 0


def test_counters_are_not_shared_between_runs():
    registry, _ = setup([[hit()]])
    first = run_agent(ControlledPlanner([search(), FinishAction(action="finish")]), ControlledGeneration(), registry, "问题")
    second = run_agent(ControlledPlanner([FinishAction(action="finish")]), ControlledGeneration(), registry, "问题")
    assert first.metrics.decision_count == 2 and first.metrics.tool_execution_count == 1
    assert second.metrics.decision_count == 1 and second.metrics.tool_execution_count == 0


def test_legacy_run_without_metrics_is_not_given_fabricated_zeros():
    old = AgentRun(uuid4(), "completed", None, [], None, [])
    adapter = TypeAdapter(AgentRun)
    restored = adapter.validate_json(adapter.dump_json(old, exclude={"metrics"}))
    assert restored.metrics is None and AgentRunRead.model_validate(restored).metrics is None
