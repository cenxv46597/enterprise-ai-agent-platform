"""受控模型和工具验证有界循环，状态、停止和引用；无数据库或网络。"""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.agents.contracts import (
    AgentInputError, FinishAction, PlannerError, PlannerTimeoutError,
    ToolAction, ToolExecutionError, ToolOutput,
)
from app.agents.engine import run_agent
from app.agents.tools import SearchArguments, ToolRegistry
from app.knowledge.retrieval import SearchHit
from app.providers.generation import GeneratedAnswer, GenerationError


def search(query="退款流程"):
    return ToolAction(action="tool", tool_name="knowledge_search", arguments={"query": query})


def hit(content="在订单详情页提交申请。"):
    return SearchHit(uuid4(), uuid4(), "说明.txt", 1, content, 7, 7 + len(content), 0.1)


class ControlledPlanner:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.states = []
    def decide(self, state):
        self.states.append(state)
        decision = next(self.decisions)
        if isinstance(decision, Exception):
            raise decision
        return decision


class ControlledGeneration:
    def __init__(self, error=None, draft=None):
        self.calls = []
        self.error, self.draft = error, draft
    def generate_answer(self, question, sources):
        self.calls.append((question, sources))
        if self.error:
            raise self.error
        return self.draft or GeneratedAnswer(answer=sources[0].content, abstained=False,
                                             citations=[{"source_id": 1, "quote": sources[0].content}])


class ControlledTool:
    name = "knowledge_search"
    description = "受控只读工具"
    argument_schema = SearchArguments
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []
    def execute(self, arguments, context=None):
        self.calls.append(arguments)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return ToolOutput(tuple(outcome))


def setup(outcomes):
    tool = ControlledTool(outcomes)
    registry = ToolRegistry()
    registry.register(tool)
    return registry, tool


def test_search_observe_finish_reuses_exact_evidence_and_citations():
    original = hit()
    registry, tool = setup([[original]])
    planner = ControlledPlanner([search(), FinishAction(action="finish")])
    generation = ControlledGeneration()
    result = run_agent(planner, generation, registry, "如何退款？")
    assert result.status == "completed" and not result.result.abstained
    assert len(tool.calls) == len(generation.calls) == 1 and len(planner.states) == 2
    assert planner.states[1]["evidence"] == [{"evidence_id": 1, "content": original.content}]
    assert planner.states[1]["observations"][0]["evidence_ids"] == [1]
    assert result.result.citations[0].chunk_id == original.chunk_id
    assert result.result.citations[0].start_char == original.start_char
    assert [item.action for item in result.steps] == ["tool", "finish"]


def test_refined_query_combines_unique_snapshots_without_overwriting_scores():
    first, second = hit("审核通过后到账。"), hit("在订单详情页申请。")
    registry, tool = setup([[first], [replace(first, distance=0.8), second]])
    planner = ControlledPlanner([search("到账"), search("申请入口"), FinishAction(action="finish")])
    generation = ControlledGeneration()
    result = run_agent(planner, generation, registry, "退款流程？")
    assert result.status == "completed" and len(generation.calls[0][1]) == 2
    assert result.steps[1].evidence_ids == [1, 2]
    assert len(tool.calls) == 2 and len(planner.states[2]["evidence"]) == 2


def test_repeated_normalized_action_stops_without_second_tool_or_generation():
    registry, tool = setup([[hit()]])
    planner = ControlledPlanner([search("退款"), search(" 退款 ")])
    generation = ControlledGeneration()
    result = run_agent(planner, generation, registry, "问题")
    assert result.status == "stopped" and result.stop_reason == "duplicate_action" and result.result is None
    assert len(tool.calls) == 1 and not generation.calls


def test_last_decision_cannot_execute_another_tool():
    registry, tool = setup([[hit()]])
    planner = ControlledPlanner([search("第一问"), search("第二问")])
    generation = ControlledGeneration()
    result = run_agent(planner, generation, registry, "问题", max_steps=2)
    assert result.status == "stopped" and result.stop_reason == "step_limit"
    assert len(tool.calls) == 1 and len(planner.states) == 2 and not generation.calls


def test_one_step_run_allows_finish_but_no_tool_execution():
    registry, tool = setup([])
    result = run_agent(ControlledPlanner([search()]), ControlledGeneration(), registry, "问题", max_steps=1)
    assert result.stop_reason == "step_limit" and not tool.calls


def test_immediate_finish_abstains_without_generation():
    registry, tool = setup([])
    generation = ControlledGeneration()
    result = run_agent(ControlledPlanner([FinishAction(action="finish")]), generation, registry, "问题")
    assert result.status == "completed" and result.result.abstained and not generation.calls and not tool.calls


@pytest.mark.parametrize("action,code", [
    (ToolAction(action="tool", tool_name="delete_files", arguments={}), "unknown_tool"),
    (ToolAction(action="tool", tool_name="knowledge_search", arguments={"query": "问题", "workspace_id": "别的库"}), "invalid_arguments"),
    (ToolAction(action="tool", tool_name="knowledge_search", arguments={"query": True}), "invalid_arguments"),
])
def test_invalid_actions_become_observations_without_execution(action, code):
    registry, tool = setup([])
    planner = ControlledPlanner([action, FinishAction(action="finish")])
    result = run_agent(planner, ControlledGeneration(), registry, "问题")
    assert not tool.calls and result.status == "failed" and result.result is None
    assert planner.states[1]["observations"][0]["error_code"] == code


def test_tool_failure_can_be_observed_then_recovered_with_new_arguments():
    registry, tool = setup([ToolExecutionError("tool_timeout"), [hit()]])
    planner = ControlledPlanner([search("第一问"), search("改写问题"), FinishAction(action="finish")])
    result = run_agent(planner, ControlledGeneration(), registry, "问题")
    assert result.status == "completed" and len(tool.calls) == 2
    assert planner.states[1]["observations"][0]["error_code"] == "tool_timeout"


def test_timed_out_action_is_not_automatically_repeated():
    registry, tool = setup([ToolExecutionError("tool_timeout")])
    result = run_agent(ControlledPlanner([search(), search()]), ControlledGeneration(), registry, "问题")
    assert result.stop_reason == "duplicate_action" and len(tool.calls) == 1


def test_all_tool_attempts_failed_cannot_be_reported_as_normal_abstention():
    registry, tool = setup([ToolExecutionError("tool_timeout")])
    generation = ControlledGeneration()
    result = run_agent(ControlledPlanner([search(), FinishAction(action="finish")]), generation, registry, "问题")
    assert result.status == "failed" and result.stop_reason == "tool_error" and result.result is None
    assert len(tool.calls) == 1 and not generation.calls


@pytest.mark.parametrize("failure,code", [(PlannerError("private"), "planner_error"), (PlannerTimeoutError("private"), "planner_timeout")])
def test_planner_faults_preserve_failed_state_without_fake_answer(failure, code):
    registry, tool = setup([])
    result = run_agent(ControlledPlanner([failure]), ControlledGeneration(), registry, "问题")
    assert result.status == "failed" and result.stop_reason == code and result.result is None
    assert "private" not in str(result.steps) and not tool.calls


def test_provider_constructed_action_is_revalidated():
    registry, tool = setup([])
    invalid = ToolAction.model_construct(action="tool", tool_name="knowledge_search", arguments="not-a-dict")
    result = run_agent(ControlledPlanner([invalid]), ControlledGeneration(), registry, "问题")
    assert result.status == "failed" and not tool.calls


def test_generation_fault_is_failed_instead_of_abstention():
    registry, _ = setup([[hit()]])
    result = run_agent(ControlledPlanner([search(), FinishAction(action="finish")]),
                       ControlledGeneration(error=GenerationError("private")), registry, "问题")
    assert result.status == "failed" and result.stop_reason == "generation_error" and result.result is None


def test_fabricated_quote_is_rejected_using_existing_citation_validator():
    registry, _ = setup([[hit()]])
    draft = GeneratedAnswer(answer="伪造", abstained=False, citations=[{"source_id": 1, "quote": "不存在的引文"}])
    result = run_agent(ControlledPlanner([search(), FinishAction(action="finish")]), ControlledGeneration(draft=draft), registry, "问题")
    assert result.status == "failed" and result.stop_reason == "generation_error"


def test_evidence_growth_limit_stops_without_partial_final_answer():
    registry, _ = setup([[hit("甲" * 10)], [hit("乙" * 11)]])
    generation = ControlledGeneration()
    result = run_agent(ControlledPlanner([search("甲"), search("乙")]), generation, registry, "问题", max_evidence_characters=20)
    assert result.stop_reason == "evidence_limit" and result.result is None and not generation.calls


def test_evidence_count_is_bounded():
    registry, _ = setup([[hit() for _ in range(21)]])
    result = run_agent(ControlledPlanner([search()]), ControlledGeneration(), registry, "问题")
    assert result.stop_reason == "evidence_limit"


def test_conflicting_snapshot_stops_instead_of_overwriting_original():
    original = hit()
    registry, _ = setup([[original], [replace(original, content="改动原文")]])
    result = run_agent(ControlledPlanner([search("甲"), search("乙")]), ControlledGeneration(), registry, "问题")
    assert result.stop_reason == "evidence_changed" and result.result is None


def test_planner_cannot_mutate_controller_state_or_tool_definitions():
    registry, _ = setup([[hit()]])
    class MutatingPlanner(ControlledPlanner):
        def decide(self, state):
            choice = super().decide(state)
            state["tools"].clear()
            state["evidence"].clear()
            return choice
    planner = MutatingPlanner([search(), FinishAction(action="finish")])
    generation = ControlledGeneration()
    result = run_agent(planner, generation, registry, "问题")
    assert result.status == "completed" and generation.calls[0][1]
    assert registry.definitions()


@pytest.mark.parametrize("options", [{"max_steps": True}, {"max_steps": 0}, {"max_steps": 9}, {"max_evidence_characters": 12001}])
def test_invalid_limits_precede_every_model_call(options):
    registry, tool = setup([])
    planner = ControlledPlanner([])
    with pytest.raises(AgentInputError):
        run_agent(planner, ControlledGeneration(), registry, "问题", **options)
    assert not planner.states and not tool.calls


def test_registry_rejects_duplicate_names():
    registry, _ = setup([])
    with pytest.raises(ValueError):
        registry.register(ControlledTool([]))
