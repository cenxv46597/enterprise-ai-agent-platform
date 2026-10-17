import type { ConversationSource } from './conversation-api';

export type TraceStep = {
  number: number; action: 'tool' | 'finish' | 'decision'; name: string;
  status: 'success' | 'error' | 'stopped'; reason: string | null;
  execution: 'entered' | 'blocked' | 'unknown' | 'not-tool';
  evidence: ConversationSource[]; calculationId: number | null;
  plannerMs: number; executionMs: number | null;
};
export type ConversationTrace = { kind: 'unavailable' } | {
  kind: 'available'; steps: TraceStep[]; reason: string | null;
  metrics: { totalMs: number; decisionCount: number; toolExecutionCount: number } | null;
};

const TOOL_NAMES: Record<string, string> = {
  knowledge_search: '知识库检索', calculator: '原文数值算术', sourced_calculator: '来源绑定算术',
  ask_user: '请求补充条件', prepare_billing: '准备标准计费规则',
};
const REASONS: Record<string, string> = {
  unknown_tool: '工具未注册，未进入执行', invalid_arguments: '工具参数未通过校验，未进入执行',
  duplicate_action: '重复动作被拦截，未进入执行', step_limit: '达到步数上限，未进入执行',
  planner_timeout: '动作决策超时', planner_error: '动作决策失败',
  tool_timeout: '工具执行超时', tool_service_error: '工具依赖服务失败', tool_capacity: '工具处理范围超限',
  tool_error: '工具结果未满足完成要求', evidence_changed: '原文证据发生变化', evidence_limit: '原文证据范围超限',
  generation_timeout: '回答生成超时', generation_error: '回答生成失败',
  billing_rule_error: '标准计费规则准备未成功', calculation_error: '算术步骤未成功',
  calculation_required: '缺少要求的算术结果', calculation_limit: '算术范围或记录数量超限',
  division_by_zero: '除数不能为零', invalid_numeric_source: '原文数值来源无效',
  invalid_user_source: '用户数值来源无效', invalid_calculation_source: '前序算术来源无效',
  invalid_clarification_source: '补充条件的规则来源无效', invalid_billing_source: '计费规则来源无效',
  incomplete_billing_rule: '计费规则不完整', unsupported_billing_rule: '计费规则超出支持范围',
};
const BLOCKED = new Set(['unknown_tool', 'invalid_arguments', 'duplicate_action', 'step_limit']);
const EXECUTION_ERRORS = new Set(['tool_timeout', 'tool_service_error', 'tool_capacity', 'evidence_changed',
  'evidence_limit', 'calculation_limit', 'division_by_zero', 'invalid_numeric_source', 'invalid_user_source',
  'invalid_calculation_source', 'invalid_clarification_source', 'invalid_billing_source',
  'incomplete_billing_rule', 'unsupported_billing_rule']);

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function invalid(): never { throw new Error('Invalid public trace'); }
function integer(value: unknown, min: number, max: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < min || (value as number) > max) return invalid();
  return value as number;
}
function duration(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return invalid();
  return value;
}
function optionalText(value: unknown, max: number): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string' || !value.trim() || Array.from(value).length > max) return invalid();
  return value;
}
function reason(value: string | null): string | null {
  return value === null ? null : Object.hasOwn(REASONS, value) ? REASONS[value] : '原因未识别，未展示原始内容';
}

// This projection deliberately excludes queries, arguments, native calls and arbitrary returned text.
// A malformed optional trace must not replace a separately verified answer with a fabricated failure.
export function parseConversationTrace(run: Record<string, unknown>, sources: ConversationSource[],
  calculationCount: number): ConversationTrace {
  try {
    const rawSteps = run.steps;
    if (!Array.isArray(rawSteps) || rawSteps.length > 8) return invalid();
    const steps = rawSteps.map((raw: unknown, index: number): TraceStep => {
      if (!object(raw) || raw.step !== index + 1
          || (raw.action !== 'tool' && raw.action !== 'finish' && raw.action !== 'decision')
          || (raw.status !== 'success' && raw.status !== 'error' && raw.status !== 'stopped')) return invalid();
      const tool = optionalText(raw.tool_name, 80);
      const code = optionalText(raw.error_code, 100);
      if ((raw.action === 'tool' ? tool === null : tool !== null)
          || (raw.status === 'success' && code !== null)
          || !Array.isArray(raw.evidence_ids) || raw.evidence_ids.length > 20) return invalid();
      const ids = raw.evidence_ids.map((id: unknown) => integer(id, 1, sources.length));
      if (new Set(ids).size !== ids.length) return invalid();
      const calculationId = raw.calculation_id === undefined || raw.calculation_id === null
        ? null : integer(raw.calculation_id, 1, calculationCount);
      if (raw.action !== 'tool' && (ids.length > 0 || calculationId !== null)) return invalid();
      if ((raw.action !== 'tool' && index !== rawSteps.length - 1)
          || (raw.action === 'decision' && raw.status !== 'error')
          || (raw.action === 'finish' && raw.status === 'stopped')
          || (raw.status !== 'success' && (ids.length > 0 || calculationId !== null))
          || ((code === 'unknown_tool' || code === 'invalid_arguments') && raw.status !== 'error')
          || ((code === 'duplicate_action' || code === 'step_limit') && raw.status !== 'stopped')) return invalid();
      const execution = raw.action !== 'tool' ? 'not-tool' : code && BLOCKED.has(code) ? 'blocked'
        : raw.status === 'success' || (code && EXECUTION_ERRORS.has(code)) ? 'entered' : 'unknown';
      if (execution === 'blocked' && (ids.length > 0 || calculationId !== null)) return invalid();
      return { number: index + 1, action: raw.action,
        name: raw.action === 'finish' ? '结束并形成回复' : raw.action === 'decision' ? '动作决策'
          : Object.hasOwn(TOOL_NAMES, tool!) ? TOOL_NAMES[tool!] : '其他工具（名称未展示）',
        status: raw.status, reason: reason(code), execution,
        evidence: ids.map(id => sources[id - 1]), calculationId,
        plannerMs: duration(raw.planner_ms), executionMs: raw.execution_ms === undefined ? null : duration(raw.execution_ms) };
    });
    const last = steps.at(-1);
    if (last && ((run.status === 'completed' && (last.action !== 'finish' || last.status !== 'success'))
        || (run.status === 'failed' && (last.action === 'tool' || last.status !== 'error'))
        || (run.status === 'stopped' && last.status !== 'stopped')
        || (run.status === 'waiting_for_user' && (last.action !== 'tool' || last.status !== 'success')))) return invalid();
    let metrics = null;
    if (run.metrics !== undefined && run.metrics !== null) {
      if (!object(run.metrics)) return invalid();
      const decisionCount = integer(run.metrics.decision_count, 0, 8);
      const toolExecutionCount = integer(run.metrics.tool_execution_count, 0, 7);
      const entered = steps.filter(step => step.execution === 'entered').length;
      const possiblyEntered = steps.filter(step => step.execution === 'entered' || step.execution === 'unknown').length;
      if (decisionCount !== steps.length || toolExecutionCount < entered || toolExecutionCount > possiblyEntered) return invalid();
      metrics = { totalMs: duration(run.metrics.total_ms), decisionCount, toolExecutionCount };
    }
    return { kind: 'available', steps, metrics, reason: reason(optionalText(run.stop_reason, 100)) };
  } catch { return { kind: 'unavailable' }; }
}
