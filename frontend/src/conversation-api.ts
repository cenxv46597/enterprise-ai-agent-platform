import {
  ApiError, getDocument, parseKnowledgeAnswer, strictUuid, webRequestOptions,
  type KnowledgeAnswerResult,
} from './api';

export type CreatedConversation = { id: string; version: 0; status: 'idle' };
export type ConversationSnapshot = {
  id: string;
  version: number;
  status: 'idle' | 'waiting_for_user' | 'ready' | 'completed';
};
export type FirstMessageRequest = { requestId: string; expectedVersion: 0; content: string };
export type ConversationSource = {
  evidenceId: number;
  chunkId: string;
  documentId: string;
  documentName: string;
  chunkIndex: number;
  startChar: number;
  endChar: number;
};
export type ConversationRequirement = {
  fieldName: string;
  label: string;
  unit: '次' | '个月' | '人' | '件';
  minValue: number;
  maxValue: number;
  ruleQuote: string;
  evidenceIds: number[];
};
export type ConversationPending = {
  kind: 'count' | 'billing';
  question: string;
  requirements: ConversationRequirement[];
  current: ConversationRequirement;
  sources: ConversationSource[];
};
export type FirstConversationReply = {
  conversationId: string;
  requestId: string;
  version: 1;
  kind: 'answered' | 'waiting' | 'failed' | 'stopped';
  answer: KnowledgeAnswerResult | null;
  pending: ConversationPending | null;
  calculationCount: number;
};

export type ConfirmedCondition = {
  fieldName: string; value: number; unit: string; requestId: string; rawText: string;
};
export type ConversationConditions = {
  version: number; status: 'waiting_for_user' | 'ready'; pending: ConversationPending;
  inputs: ConfirmedCondition[];
};
export type ConditionRequest = { requestId: string; expectedVersion: number; value: string };
export const CONDITION_UNCONFIRMED_MESSAGE = '补充条件结果未确认；可能已保存，已暂停后续补充，不自动重试。';

export const CONVERSATION_CREATE_UNCONFIRMED_MESSAGE = '会话创建结果未确认；可能已保存空会话，未自动再次创建。';
export const CONVERSATION_MESSAGE_UNCONFIRMED_MESSAGE = '首条消息结果未确认；可能已调用模型并保存会话，未自动重试。';

export class ConversationUnconfirmedError extends ApiError {
  readonly stage: Stage;

  constructor(stage: Stage, status: number | null = null) {
    super(stage === 'create' ? CONVERSATION_CREATE_UNCONFIRMED_MESSAGE
      : stage === 'condition' ? CONDITION_UNCONFIRMED_MESSAGE : CONVERSATION_MESSAGE_UNCONFIRMED_MESSAGE, status);
    this.name = 'ConversationUnconfirmedError';
    this.stage = stage;
  }
}

type Stage = 'create' | 'message' | 'condition';
const MESSAGE_WAIT_MS = 2_430_000;
// Match Python's Unicode whitespace used by billing/rules.py, including U+0085.
const RULE_SPACE = '[\\u0009-\\u000d\\u001c-\\u0020\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]';
const RULE_PRICE = '(?:0|[1-9][0-9]{0,11})(?:\\.[0-9]{1,2})?';
const STANDARD_BILLING_RULE = new RegExp(`^(月费${RULE_SPACE}*(${RULE_PRICE})${RULE_SPACE}*元)${RULE_SPACE}*[，,；;。]${RULE_SPACE}*(每次上门服务费${RULE_SPACE}*(${RULE_PRICE})${RULE_SPACE}*元)${RULE_SPACE}*[。.]?$`);
const RULE_EDGE_SPACE = new RegExp(`^${RULE_SPACE}+|${RULE_SPACE}+$`, 'g');

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function invalid(): never {
  throw new Error('Invalid public conversation response');
}

function text(value: unknown, minimum: number, maximum: number, nonblank = true): string {
  if (typeof value !== 'string' || (nonblank && !value.trim())) return invalid();
  let length = 0;
  for (const _character of value) {
    length += 1;
    if (length > maximum) return invalid();
  }
  return length < minimum ? invalid() : value;
}

function integer(value: unknown, minimum: number, maximum = Number.MAX_SAFE_INTEGER): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) return invalid();
  return value as number;
}

function scopedPath(workspaceId: string, knowledgeBaseId: string): string {
  return `/api/v1/workspaces/${strictUuid(workspaceId)}/knowledge-bases/${strictUuid(knowledgeBaseId)}/conversations`;
}

async function request(
  path: string, method: 'POST' | 'GET', stage: Stage, expectedStatus: number,
  parentSignal: AbortSignal, waitMs: number, body?: string,
): Promise<unknown> {
  if (parentSignal.aborted) throw new ConversationUnconfirmedError(stage);
  const controller = new AbortController();
  const cancel = () => controller.abort();
  parentSignal.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(cancel, waitMs);
  try {
    const options: RequestInit = { method };
    if (body !== undefined) {
      options.headers = { 'Content-Type': 'application/json' };
      options.body = body;
    }
    const response = await fetch(path, webRequestOptions(options, controller.signal));
    if (controller.signal.aborted) throw new ConversationUnconfirmedError(stage);
    const messages: Record<number, string> = {
      401: '登录已失效，请重新登录。',
      403: '会话请求被拒绝，请检查登录与浏览器来源。',
      404: '该知识库或会话不存在，或你已无权访问，请返回文档列表。',
      409: '会话状态、版本或来源已变化，请先显式核对状态；未自动重试，本响应不能确认是否已产生费用。',
      422: '会话请求参数未被接受，请核对输入；未自动重试，本响应不能确认是否已产生费用。',
    };
    if (Object.hasOwn(messages, response.status)) throw new ApiError(messages[response.status], response.status);
    if (response.status !== expectedStatus) throw new ConversationUnconfirmedError(stage, response.status);
    const payload: unknown = await response.json();
    if (controller.signal.aborted) throw new ConversationUnconfirmedError(stage);
    return payload;
  } catch (failure) {
    if (controller.signal.aborted) throw new ConversationUnconfirmedError(stage);
    if (failure instanceof ApiError) throw failure;
    throw new ConversationUnconfirmedError(stage);
  } finally {
    clearTimeout(timer);
    parentSignal.removeEventListener('abort', cancel);
  }
}

function parseSnapshot(payload: unknown, expectedId?: string): ConversationSnapshot {
  if (!object(payload)) return invalid();
  const id = strictUuid(payload.conversation_id);
  const version = integer(payload.version, 0, 200);
  const status = payload.status;
  if ((expectedId !== undefined && id !== expectedId)
      || (status !== 'idle' && status !== 'waiting_for_user' && status !== 'ready' && status !== 'completed')
      || (version === 0 && status !== 'idle')
      || (status === 'idle' ? payload.task !== null : !object(payload.task))) return invalid();
  return { id, version, status };
}

export async function createConversation(
  workspaceId: string, knowledgeBaseId: string, signal: AbortSignal,
): Promise<CreatedConversation> {
  const payload = await request(scopedPath(workspaceId, knowledgeBaseId), 'POST', 'create', 201, signal, 10_000);
  try {
    const snapshot = parseSnapshot(payload);
    if (!object(payload) || snapshot.version !== 0 || snapshot.status !== 'idle'
        || !Array.isArray(payload.recent_messages) || payload.recent_messages.length !== 0
        || payload.billing_result !== null || signal.aborted) return invalid();
    return { id: snapshot.id, version: 0, status: 'idle' };
  } catch {
    throw new ConversationUnconfirmedError('create');
  }
}

export async function getConversationSnapshot(
  workspaceId: string, knowledgeBaseId: string, conversationId: string, signal: AbortSignal,
): Promise<ConversationSnapshot> {
  const id = strictUuid(conversationId);
  const payload = await request(`${scopedPath(workspaceId, knowledgeBaseId)}/${id}`, 'GET', 'message', 200, signal, 10_000);
  try {
    if (signal.aborted) return invalid();
    return parseSnapshot(payload, id);
  } catch {
    throw new ConversationUnconfirmedError('message');
  }
}

function source(value: unknown, evidenceId: number): ConversationSource {
  if (!object(value)) return invalid();
  const startChar = integer(value.start_char, 0);
  const endChar = integer(value.end_char, 1);
  if (endChar <= startChar || endChar - startChar > 12000) return invalid();
  return {
    evidenceId,
    chunkId: strictUuid(value.chunk_id), documentId: strictUuid(value.document_id),
    documentName: text(value.document_name, 1, 255), chunkIndex: integer(value.chunk_index, 1),
    startChar, endChar,
  };
}

function sources(value: unknown, numbered: boolean): ConversationSource[] {
  if (!Array.isArray(value) || value.length > 20) return invalid();
  const chunks = new Set<string>();
  const documents = new Map<string, string>();
  let total = 0;
  return value.map((item: unknown, index: number) => {
    if (numbered && (!object(item) || item.evidence_id !== index + 1)) return invalid();
    const parsed = source(item, index + 1);
    if (chunks.has(parsed.chunkId)
        || (documents.has(parsed.documentId) && documents.get(parsed.documentId) !== parsed.documentName)) return invalid();
    total += parsed.endChar - parsed.startChar;
    if (total > 12000) return invalid();
    chunks.add(parsed.chunkId);
    documents.set(parsed.documentId, parsed.documentName);
    return parsed;
  });
}

function sameSource(left: ConversationSource, right: ConversationSource): boolean {
  return left.evidenceId === right.evidenceId && left.chunkId === right.chunkId
    && left.documentId === right.documentId && left.documentName === right.documentName
    && left.chunkIndex === right.chunkIndex && left.startChar === right.startChar && left.endChar === right.endChar;
}

function requirement(value: unknown, evidence: ConversationSource[]): ConversationRequirement {
  if (!object(value)) return invalid();
  const fieldName = text(value.field_name, 1, 40);
  const unit = value.unit;
  if (!/^[a-z][a-z0-9_]{0,39}$/.test(fieldName)
      || (unit !== '次' && unit !== '个月' && unit !== '人' && unit !== '件')
      || !Array.isArray(value.evidence_ids) || value.evidence_ids.length < 1 || value.evidence_ids.length > 20) return invalid();
  const evidenceIds = value.evidence_ids.map((item: unknown) => integer(item, 1, evidence.length));
  const minValue = integer(value.min_value, 0, 9999);
  const maxValue = integer(value.max_value, 0, 9999);
  const ruleQuote = text(value.rule_quote, 1, 400);
  if (new Set(evidenceIds).size !== evidenceIds.length || minValue > maxValue
      || !evidenceIds.some((id) => Array.from(ruleQuote).length <= evidence[id - 1].endChar - evidence[id - 1].startChar)) return invalid();
  return { fieldName, label: text(value.label, 1, 80), unit, minValue, maxValue, ruleQuote, evidenceIds };
}

function sameRequirement(left: ConversationRequirement, right: ConversationRequirement): boolean {
  return left.fieldName === right.fieldName && left.label === right.label && left.unit === right.unit
    && left.minValue === right.minValue && left.maxValue === right.maxValue && left.ruleQuote === right.ruleQuote
    && left.evidenceIds.length === right.evidenceIds.length
    && left.evidenceIds.every((id, index) => id === right.evidenceIds[index]);
}

function canonicalDecimal(value: string): string {
  return value.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
}

function validateBilling(plan: unknown, requirements: ConversationRequirement[], evidence: ConversationSource[]): void {
  if (!object(plan) || plan.workflow !== 'monthly_visit_v1' || requirements.length !== 2) return invalid();
  const ruleId = integer(plan.rule_evidence_id, 1, evidence.length);
  const ruleQuote = text(plan.rule_quote, 1, 400);
  const roles = [['visit_count', '次', 0, 9999], ['billing_months', '个月', 1, 12]] as const;
  requirements.forEach((item, index) => {
    const role = roles[index];
    if (item.fieldName !== role[0] || item.unit !== role[1] || item.minValue !== role[2] || item.maxValue !== role[3]
        || item.ruleQuote !== ruleQuote || item.evidenceIds.length !== 1 || item.evidenceIds[0] !== ruleId) return invalid();
  });
  const rule = ruleQuote.replace(RULE_EDGE_SPACE, '').match(STANDARD_BILLING_RULE);
  if (!rule) return invalid();
  const reference = evidence[ruleId - 1];
  for (const [rate, unit, quote, price] of [
    [plan.monthly_fee, '元/个月', rule[1], rule[2]], [plan.visit_fee, '元/次', rule[3], rule[4]],
  ] as const) {
    if (!object(rate) || rate.unit !== unit || rate.evidence_id !== ruleId || rate.quote !== quote
        || typeof rate.value !== 'string' || !/^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$/.test(rate.value)
        || canonicalDecimal(rate.value) !== canonicalDecimal(price)) return invalid();
    const bound = source(rate, ruleId);
    if (bound.chunkId !== reference.chunkId || bound.documentId !== reference.documentId
        || bound.documentName !== reference.documentName || bound.chunkIndex !== reference.chunkIndex
        || bound.startChar < reference.startChar || bound.endChar > reference.endChar
        || bound.endChar - bound.startChar !== Array.from(quote).length) return invalid();
  }
}

function pending(value: unknown, question: string, runSources: ConversationSource[], confirmedCount = 0,
  currentIndex = 0): ConversationPending {
  if (!object(value) || value.kind !== undefined || value.question !== question
      || !Array.isArray(value.confirmed_inputs) || value.confirmed_inputs.length !== confirmedCount) return invalid();
  const evidence = sources(value.evidence, false);
  if (evidence.length === 0 || evidence.length !== runSources.length
      || evidence.some((item, index) => !sameSource(item, runSources[index]))) return invalid();
  const current = requirement(value.clarification, evidence);
  const rawRequirements = value.clarifications === undefined ? [] : value.clarifications;
  if (!Array.isArray(rawRequirements) || rawRequirements.length > 2) return invalid();
  if (value.billing_plan === null || value.billing_plan === undefined) {
    if (rawRequirements.length !== 0) return invalid();
    return { kind: 'count', question, current, requirements: [current], sources: evidence };
  }
  const requirements = rawRequirements.map((item: unknown) => requirement(item, evidence));
  validateBilling(value.billing_plan, requirements, evidence);
  if (!sameRequirement(current, requirements[currentIndex])) return invalid();
  return { kind: 'billing', question, current, requirements, sources: evidence };
}

function parseFirstReply(
  payload: unknown, conversationId: string, requestId: string, question: string,
): { reply: FirstConversationReply; documents: Map<string, string> } {
  if (!object(payload) || strictUuid(payload.conversation_id) !== conversationId
      || strictUuid(payload.request_id) !== requestId || payload.version !== 1
      || (payload.status !== 'idle' && payload.status !== 'waiting_for_user')
      || payload.billing_result !== null
      || (payload.execution_deferred !== undefined && payload.execution_deferred !== null)
      || !object(payload.run)) return invalid();
  const run = payload.run;
  strictUuid(run.run_id);
  if (run.status !== 'completed' && run.status !== 'waiting_for_user'
      && run.status !== 'failed' && run.status !== 'stopped') return invalid();
  const evidence = sources(run.evidence, true);
  if (!Array.isArray(run.calculations) || run.calculations.length > 7
      || run.calculations.some((item: unknown, index: number) => !object(item) || item.calculation_id !== index + 1)) return invalid();
  const reply: FirstConversationReply = {
    conversationId, requestId, version: 1, kind: 'failed', answer: null, pending: null,
    calculationCount: run.calculations.length,
  };
  const documents = new Map<string, string>();
  if (run.status === 'waiting_for_user') {
    if (payload.status !== 'waiting_for_user' || run.result !== null) return invalid();
    reply.kind = 'waiting';
    reply.pending = pending(payload.task, question, evidence);
    for (const item of reply.pending.sources) documents.set(item.documentId, item.documentName);
  } else {
    if (payload.status !== 'idle' || payload.task !== null) return invalid();
    if (run.status === 'completed') {
      const answer = parseKnowledgeAnswer(run.result);
      if (payload.reply !== answer.answer) return invalid();
      const byChunk = new Map(evidence.map((item) => [item.chunkId, item]));
      for (const citation of answer.citations) {
        const bound = byChunk.get(citation.chunkId);
        if (!bound || citation.documentId !== bound.documentId || citation.documentName !== bound.documentName
            || citation.chunkIndex !== bound.chunkIndex || citation.startChar < bound.startChar || citation.endChar > bound.endChar) return invalid();
        documents.set(citation.documentId, citation.documentName);
      }
      reply.kind = 'answered';
      reply.answer = answer;
    } else {
      if (run.result !== null) return invalid();
      reply.kind = run.status;
    }
  }
  return { reply, documents };
}

export async function sendFirstConversationMessage(
  workspaceId: string, knowledgeBaseId: string, conversationId: string,
  first: FirstMessageRequest, signal: AbortSignal,
): Promise<FirstConversationReply> {
  const id = strictUuid(conversationId);
  const requestId = strictUuid(first.requestId);
  const path = `${scopedPath(workspaceId, knowledgeBaseId)}/${id}/messages`;
  let content: string;
  try {
    if (first.expectedVersion !== 0 || typeof first.content !== 'string') return invalid();
    content = text(first.content.trim(), 1, 2000);
  } catch {
    throw new ApiError('请输入 1 到 2000 字符的首条消息，并使用新会话的初始版本。');
  }
  const payload = await request(path, 'POST', 'message', 200, signal, MESSAGE_WAIT_MS, JSON.stringify({
    request_id: requestId, expected_version: 0, content, mode: 'continue',
    confirm_billing_rule: false, response_mode: 'knowledge',
  }));
  try {
    if (signal.aborted) return invalid();
    const parsed = parseFirstReply(payload, id, requestId, content);
    for (const [documentId, documentName] of parsed.documents) {
      if (signal.aborted) return invalid();
      const document = await getDocument(workspaceId, knowledgeBaseId, documentId, signal);
      if (signal.aborted || document.originalFilename !== documentName || document.status !== 'ready') return invalid();
    }
    if (signal.aborted) return invalid();
    return parsed.reply;
  } catch (failure) {
    if (!signal.aborted && failure instanceof ApiError && failure.status === 401) throw failure;
    throw new ConversationUnconfirmedError('message', failure instanceof ApiError ? failure.status : null);
  }
}

// Only a verified POST receipt can advance these conditions. A metadata GET cannot.
export async function submitConversationCondition(
  workspaceId: string, knowledgeBaseId: string, conversationId: string,
  previous: ConversationConditions, submitted: ConditionRequest, signal: AbortSignal,
): Promise<ConversationConditions> {
  const id = strictUuid(conversationId);
  const requestId = strictUuid(submitted.requestId);
  const current = previous.pending.current;
  if (previous.status !== 'waiting_for_user' || submitted.expectedVersion !== previous.version
      || !Number.isSafeInteger(previous.version) || previous.version < 1 || previous.version >= 200
      || typeof submitted.value !== 'string' || submitted.value.trim() !== submitted.value
      || !/^(?:0|[1-9][0-9]{0,3})$/.test(submitted.value)
      || Number(submitted.value) < current.minValue || Number(submitted.value) > current.maxValue
      || previous.inputs.some(item => item.requestId === requestId)) {
    throw new ApiError('请输入当前字段范围内的整数，并使用已确认的会话版本。');
  }
  const content = `${submitted.value}${current.unit}`;
  const payload = await request(`${scopedPath(workspaceId, knowledgeBaseId)}/${id}/messages`,
    'POST', 'condition', 200, signal, 10_000, JSON.stringify({
      request_id: requestId, expected_version: submitted.expectedVersion, content,
      mode: 'continue', confirm_billing_rule: false, response_mode: 'knowledge',
    }));
  try {
    if (signal.aborted || !object(payload) || payload.conversation_id !== id || payload.request_id !== requestId
        || payload.version !== previous.version + 1 || payload.run !== null || payload.billing_result !== null
        || payload.answer_preference !== null || !object(payload.task)) return invalid();
    const rawInputs = payload.task.confirmed_inputs;
    if (!Array.isArray(rawInputs) || rawInputs.length !== previous.inputs.length + 1) return invalid();
    const expected: ConfirmedCondition[] = [...previous.inputs,
      { fieldName: current.fieldName, value: Number(submitted.value), unit: current.unit, requestId, rawText: content }];
    rawInputs.forEach((item: unknown, index: number) => {
      const bound = expected[index];
      if (!object(item) || item.field_name !== bound.fieldName || item.value !== bound.value
          || item.unit !== bound.unit || item.request_id !== bound.requestId || item.raw_text !== bound.rawText
          || item.provenance !== 'user_message') return invalid();
    });
    const ready = expected.length === previous.pending.requirements.length;
    if (payload.status !== (ready ? 'ready' : 'waiting_for_user')
        || payload.execution_deferred !== (ready ? 'explicit_execution_required' : null)) return invalid();
    const next = pending(payload.task, previous.pending.question, previous.pending.sources, expected.length,
      ready ? expected.length - 1 : expected.length);
    if (next.kind !== previous.pending.kind || next.requirements.length !== previous.pending.requirements.length
        || next.requirements.some((item, index) => !sameRequirement(item, previous.pending.requirements[index]))) return invalid();
    const documents = new Map(next.sources.map(item => [item.documentId, item.documentName]));
    for (const [documentId, documentName] of documents) {
      if (signal.aborted) return invalid();
      const document = await getDocument(workspaceId, knowledgeBaseId, documentId, signal);
      if (signal.aborted || document.originalFilename !== documentName || document.status !== 'ready') return invalid();
    }
    if (signal.aborted) return invalid();
    return { version: payload.version as number, status: ready ? 'ready' : 'waiting_for_user', pending: next, inputs: expected };
  } catch (failure) {
    if (!signal.aborted && failure instanceof ApiError && failure.status === 401) throw failure;
    throw new ConversationUnconfirmedError('condition', failure instanceof ApiError ? failure.status : null);
  }
}
