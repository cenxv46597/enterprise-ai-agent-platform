"""显式决策、执行、观察循环；步数与证据均有界，停止不伪装成任务完成。"""

import json
import logging
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import (
    MAX_AGENT_CHARACTERS, MAX_AGENT_EVIDENCE, MAX_AGENT_STEPS, AgentEvidence, AgentInputError,
    AgentRun, AgentStep, FinishAction, PlannerConfigurationError, PlannerError,
    PlannerProvider, PlannerTimeoutError, ToolAction, ToolContext, ToolExecutionError, validate_decision,
)
from app.agents.calculation_contracts import AgentCalculation, MAX_CALCULATIONS
from app.agents.sourced_contracts import SourcedAgentCalculation, SourcedCalculation
from app.agents.calculation_answers import answer_from_calculations
from app.agents.document_status_answers import answer_from_document_status
from app.agents.tools import ToolRegistry
from app.db.models import KnowledgeBase
from app.knowledge.answers import AnswerResult, answer_from_hits
from app.knowledge.document_candidates import DocumentCandidateList
from app.knowledge.document_status import DocumentProcessingStatus
from app.knowledge.retrieval import RetrievalNotFoundError, SearchHit
from app.providers.generation import GenerationConfigurationError, GenerationError, GenerationProvider, GenerationTimeoutError
from app.conversations.contracts import EvidenceSnapshot, PendingTask
from app.conversations.contracts import UserCount
from app.conversations.document_selection import DocumentSelectionTask


logger = logging.getLogger(__name__)


def validate_scope(session: Session, workspace_id: UUID, knowledge_base_id: UUID) -> None:
    """在首次决策模型调用前校验范围；当前不代表身份认证。"""
    with session.begin():
        found = session.scalar(select(KnowledgeBase.id).where(
            KnowledgeBase.id == knowledge_base_id, KnowledgeBase.workspace_id == workspace_id,
        ).with_for_update(read=True))
        if found is None:
            raise RetrievalNotFoundError("知识库不存在")


def _snapshot(hit: SearchHit) -> tuple:
    # 不比较距离或融合分数，它们可随不同搜索问题变化。
    return (hit.document_id, hit.document_name, hit.chunk_index, hit.content, hit.start_char, hit.end_char)


def _planner_observation(step: AgentStep) -> dict:
    excluded = {"planner_ms", "execution_ms", "reranking"}
    if step.document_candidates is None:
        excluded.add("document_candidates")
    if step.document_status is None:
        excluded.add("document_status")
    # 候选含 UUID 和时间；下一轮状态必须是可 JSON 编码的副本。
    return step.model_dump(mode="json", exclude=excluded)


def _document_status_answer(steps: list[AgentStep]) -> AnswerResult:
    """首版只完成一份已明确文档的状态任务；不选择同名候选或掩盖工具故障。"""
    if any(step.action == "tool" and step.status == "error" for step in steps):
        raise ToolExecutionError("tool_error")
    results = [step.document_status for step in steps if step.document_status is not None]
    if not results:
        raise ToolExecutionError("document_status_required")
    if len(results) != 1:
        raise ToolExecutionError("document_selection_required")
    result = results[0]
    candidates = next((step.document_candidates for step in reversed(steps)
                       if step.document_candidates is not None), None)
    if candidates is not None and (
        candidates.has_more or len(candidates.candidates) != 1
        or candidates.candidates[0].document_id != result.document_id
    ):
        raise ToolExecutionError("document_selection_required")
    return answer_from_document_status(result)


def run_agent(
    planner: PlannerProvider, generation: GenerationProvider, registry: ToolRegistry, question: str, *,
    max_steps: int = 4, max_evidence_characters: int = MAX_AGENT_CHARACTERS,
    conversation_context: dict | None = None,
    initial_evidence: tuple[SearchHit, ...] = (), user_inputs: tuple[UserCount, ...] = (),
    require_calculation: bool = False,
    response_mode: Literal["knowledge", "document_status"] = "knowledge",
) -> AgentRun:
    """调用方授权、绑定范围及任务类型；模型不能切换程序指定的回答模式。"""
    if (
        not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000
        or type(max_steps) is not int or not 1 <= max_steps <= MAX_AGENT_STEPS
        or type(max_evidence_characters) is not int or not 1 <= max_evidence_characters <= MAX_AGENT_CHARACTERS
        or response_mode not in ("knowledge", "document_status")
        or (response_mode == "document_status" and require_calculation)
    ):
        raise AgentInputError("智能体问题、步数或证据预算无效")
    question = question.strip()
    identifier = uuid4()
    steps: list[AgentStep] = []
    if (not isinstance(initial_evidence, tuple) or not all(isinstance(item, SearchHit) for item in initial_evidence)
            or len(initial_evidence) > MAX_AGENT_EVIDENCE
            or len({item.chunk_id for item in initial_evidence}) != len(initial_evidence)
            or sum(len(item.content) for item in initial_evidence) > max_evidence_characters
            or not isinstance(user_inputs, tuple) or len(user_inputs) > 1
            or not all(isinstance(item, UserCount) for item in user_inputs)):
        raise AgentInputError("恢复运行的输入或原文预算无效")
    # 输入来自服务层的已确认任务，不从 conversation_context 或模型输出提升为可信来源。
    evidence: dict[UUID, SearchHit] = {item.chunk_id: item for item in initial_evidence}
    seen: set[str] = set()
    call_ids: set[str] = set()
    tool_messages: list[dict] = []
    calculations: list[AgentCalculation | SourcedAgentCalculation] = []
    definitions = registry.definitions()

    def observe(decision: ToolAction, *, error_code: str | None = None, evidence_ids: list[int] | None = None,
                calculation: AgentCalculation | SourcedAgentCalculation | None = None,
                document_candidates: DocumentCandidateList | None = None,
                document_status: DocumentProcessingStatus | None = None) -> None:
        """回传原生调用的同一编号；只写本次运行，不写文件或共享服务对象。"""
        if decision.native_call is None:
            return
        call = decision.native_call
        content_by_id = {index: hit.content for index, hit in enumerate(evidence.values(), 1)}
        result = ({"status": "error", "error_code": error_code} if error_code else
                  {"status": "success", "evidence": [{"evidence_id": index, "content": content_by_id[index]}
                                                        for index in evidence_ids or []]})
        if calculation is not None:
            result["calculation"] = calculation.model_dump(mode="json")
        if document_candidates is not None:
            result["document_candidates"] = document_candidates.model_dump(mode="json")
        if document_status is not None:
            result["document_status"] = document_status.model_dump(mode="json")
        tool_messages.extend([
            {"role": "assistant", "content": call.content, "tool_calls": [
                {"id": call.id, "type": "function", "function": {
                    "name": decision.tool_name, "arguments": call.arguments_json}}]},
            {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)},
        ])

    def outcome(status: Literal["completed", "stopped", "failed", "waiting_for_user"], reason: str | None,
                result: AnswerResult | None = None, pending_task: PendingTask | None = None,
                document_selection: DocumentSelectionTask | None = None) -> AgentRun:
        summaries = [AgentEvidence(evidence_id=index, chunk_id=hit.chunk_id, document_id=hit.document_id,
                                   document_name=hit.document_name, chunk_index=hit.chunk_index,
                                   start_char=hit.start_char, end_char=hit.end_char)
                     for index, hit in enumerate(evidence.values(), 1)]
        return AgentRun(identifier, status, reason, steps, result, summaries, calculations, pending_task,
                        pending_document_selection=document_selection)

    def stopped(reason: str) -> AgentRun:
        logger.info("智能体运行停止，运行 ID：%s，原因：%s", identifier, reason)
        return outcome("stopped", reason)

    def failed(reason: str) -> AgentRun:
        logger.warning("智能体运行失败，运行 ID：%s，原因：%s", identifier, reason)
        return outcome("failed", reason)

    for number in range(1, max_steps + 1):
        # 本次有界状态每轮重新构造；适配器不能修改控制器持有的对象。
        state = {
            "question": question, "remaining_steps": max_steps - number + 1,
            "tools": definitions,
            "observations": [_planner_observation(step) for step in steps],
            "evidence": [{"evidence_id": index, "content": hit.content} for index, hit in enumerate(evidence.values(), 1)],
            "tool_messages": tool_messages,
            "calculations": [calculation.model_dump(mode="json") for calculation in calculations],
        }
        if conversation_context is not None:
            state["conversation_context"] = conversation_context
        if response_mode == "document_status":
            state["response_mode"] = response_mode
        if user_inputs:
            state["confirmed_inputs"] = [item.model_dump(mode="json") for item in user_inputs]
        started = monotonic()
        try:
            # definitions 也复制，避免其他适配器改动注册工具描述。
            decision = validate_decision(planner.decide(json.loads(json.dumps(state, ensure_ascii=False))))
            if isinstance(decision, ToolAction) and decision.native_call is not None:
                if decision.native_call.id in call_ids:
                    raise PlannerError("模型复用了已有调用编号")
                call_ids.add(decision.native_call.id)
        except PlannerConfigurationError:
            raise
        except PlannerError as error:
            code = "planner_timeout" if isinstance(error, PlannerTimeoutError) else "planner_error"
            steps.append(AgentStep(step=number, action="decision", status="error", error_code=code,
                                   planner_ms=(monotonic() - started) * 1000))
            return failed(code)
        planner_ms = (monotonic() - started) * 1000
        if isinstance(decision, FinishAction):
            if response_mode == "document_status":
                started = monotonic()
                try:
                    answer = _document_status_answer(steps)
                except ToolExecutionError as error:
                    steps.append(AgentStep(step=number, action="finish", status="error", error_code=error.code,
                                           planner_ms=planner_ms, execution_ms=(monotonic() - started) * 1000))
                    return failed(error.code)
                steps.append(AgentStep(step=number, action="finish", status="success", planner_ms=planner_ms,
                                       execution_ms=(monotonic() - started) * 1000))
                return outcome("completed", None, answer)
            last_billing = next((step for step in reversed(steps) if step.tool_name == "prepare_billing"), None)
            if last_billing is not None and last_billing.status != "success":
                steps.append(AgentStep(step=number, action="finish", status="error", error_code="billing_rule_error",
                                       planner_ms=planner_ms))
                return failed("billing_rule_error")
            last_calculation = next((step for step in reversed(steps)
                                     if step.tool_name in ("calculator", "sourced_calculator")), None)
            if last_calculation is not None and last_calculation.status != "success":
                # 计算没有完成，不能让普通生成流程自行给出一个数值答案绕过工具故障。
                steps.append(AgentStep(step=number, action="finish", status="error", error_code="calculation_error",
                                       planner_ms=planner_ms))
                return failed("calculation_error")
            if require_calculation and not calculations:
                steps.append(AgentStep(step=number, action="finish", status="error", error_code="calculation_required",
                                       planner_ms=planner_ms))
                return failed("calculation_required")
            if (not evidence and any(step.action == "tool" and step.status == "error" for step in steps)
                    and not any(step.action == "tool" and step.status == "success"
                                and step.document_candidates is None and step.document_status is None for step in steps)):
                # 元数据成功不代表取得正文，不能掩盖正文工具失败。
                steps.append(AgentStep(step=number, action="finish", status="error", error_code="tool_error",
                                       planner_ms=planner_ms))
                return failed("tool_error")
            started = monotonic()
            try:
                answer = (answer_from_calculations(calculations) if calculations else
                          answer_from_hits(generation, question, list(evidence.values()),
                                           max_context_characters=max_evidence_characters))
            except GenerationConfigurationError:
                raise
            except GenerationError as error:
                code = "generation_timeout" if isinstance(error, GenerationTimeoutError) else "generation_error"
                steps.append(AgentStep(step=number, action="finish", status="error", error_code=code,
                                       planner_ms=planner_ms, execution_ms=(monotonic() - started) * 1000))
                return failed(code)
            steps.append(AgentStep(step=number, action="finish", status="success", planner_ms=planner_ms,
                                   execution_ms=(monotonic() - started) * 1000))
            return outcome("completed", None, answer)

        call_id = decision.native_call.id if decision.native_call else None
        try:
            arguments = registry.validate(decision.tool_name, decision.arguments)
        except ToolExecutionError as error:
            steps.append(AgentStep(step=number, action="tool", tool_name=decision.tool_name,
                                   tool_call_id=call_id, status="error", error_code=error.code, planner_ms=planner_ms))
            observe(decision, error_code=error.code)
            continue
        normalized = arguments.model_dump()
        query = normalized.get("query", normalized.get("filename_query"))
        fingerprint = json.dumps([decision.tool_name, normalized], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if fingerprint in seen:
            steps.append(AgentStep(step=number, action="tool", tool_name=decision.tool_name, query=query,
                                   tool_call_id=call_id, status="stopped", error_code="duplicate_action", planner_ms=planner_ms))
            return stopped("duplicate_action")
        # 最后一次决策若仍要求工具，停止，不支付一次无法继续观察的调用。
        if number == max_steps:
            steps.append(AgentStep(step=number, action="tool", tool_name=decision.tool_name, query=query,
                                   tool_call_id=call_id, status="stopped", error_code="step_limit", planner_ms=planner_ms))
            return stopped("step_limit")
        seen.add(fingerprint)  # 包括随后超时的动作；不自动重复请求。
        started = monotonic()
        try:
            output = registry.execute(decision.tool_name, arguments,
                                      ToolContext(tuple(evidence.values()), user_inputs, tuple(calculations)))
        except ToolExecutionError as error:
            steps.append(AgentStep(step=number, action="tool", tool_name=decision.tool_name, query=query,
                                   tool_call_id=call_id, status="error", error_code=error.code, planner_ms=planner_ms,
                                   execution_ms=(monotonic() - started) * 1000))
            observe(decision, error_code=error.code)
            continue
        proposed = dict(evidence)
        reason = None
        for hit in output.hits:
            if hit.chunk_id in proposed and _snapshot(proposed[hit.chunk_id]) != _snapshot(hit):
                reason = "evidence_changed"
                break
            proposed.setdefault(hit.chunk_id, hit)
        if reason is None and (len(proposed) > MAX_AGENT_EVIDENCE or
                               sum(len(hit.content) for hit in proposed.values()) > max_evidence_characters):
            reason = "evidence_limit"
        if reason:
            steps.append(AgentStep(step=number, action="tool", tool_name=decision.tool_name, query=query,
                                   tool_call_id=call_id, status="stopped", error_code=reason, planner_ms=planner_ms,
                                   execution_ms=(monotonic() - started) * 1000, reranking=output.reranking))
            return stopped(reason)
        evidence = proposed
        if output.clarification is not None:
            task = PendingTask(question=question, clarification=output.clarification,
                               billing_plan=output.billing_plan, clarifications=list(output.clarifications), evidence=[
                EvidenceSnapshot(chunk_id=hit.chunk_id, document_id=hit.document_id,
                                 document_name=hit.document_name, chunk_index=hit.chunk_index,
                                 content=hit.content, start_char=hit.start_char, end_char=hit.end_char)
                for hit in evidence.values()
            ])
            steps.append(AgentStep(step=number, action="tool", tool_name=decision.tool_name,
                                   tool_call_id=call_id, status="success", planner_ms=planner_ms,
                                   execution_ms=(monotonic() - started) * 1000))
            return outcome("waiting_for_user", None, pending_task=task)
        calculation = None
        if output.calculation is not None:
            if len(calculations) >= MAX_CALCULATIONS:
                steps.append(AgentStep(step=number, action="tool", tool_name=decision.tool_name, query=query,
                                       tool_call_id=call_id, status="stopped", error_code="calculation_limit",
                                       planner_ms=planner_ms, execution_ms=(monotonic() - started) * 1000))
                return stopped("calculation_limit")
            result_class = SourcedAgentCalculation if isinstance(output.calculation, SourcedCalculation) else AgentCalculation
            calculation = result_class(**output.calculation.model_dump(), calculation_id=len(calculations) + 1)
            calculations.append(calculation)
        ids = {chunk_id: index for index, chunk_id in enumerate(evidence, 1)}
        steps.append(AgentStep(step=number, action="tool", tool_name=decision.tool_name, query=query,
                               tool_call_id=call_id, status="success", evidence_ids=[ids[hit.chunk_id] for hit in output.hits],
                               calculation_id=calculation.calculation_id if calculation else None,
                               planner_ms=planner_ms, execution_ms=(monotonic() - started) * 1000,
                               reranking=output.reranking, document_candidates=output.document_candidates,
                               document_status=output.document_status))
        observe(decision, evidence_ids=steps[-1].evidence_ids, calculation=calculation,
                document_candidates=output.document_candidates, document_status=output.document_status)
        if response_mode == "document_status" and output.document_candidates is not None:
            candidates = output.document_candidates
            if any(step.status == "error" for step in steps):
                return failed("tool_error")
            if not candidates.candidates:
                return outcome("completed", None, AnswerResult("当前知识库没有匹配的文件，请提供更具体的文件名线索。", False, []))
            if candidates.has_more or len(candidates.candidates) > 1:
                return outcome("waiting_for_user", None, document_selection=DocumentSelectionTask(
                    question=question, filename_query=arguments.filename_query, candidates=candidates,
                ))
    return stopped("step_limit")
