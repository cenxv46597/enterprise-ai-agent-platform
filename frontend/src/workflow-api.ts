import { ApiError, getDocument, listDocumentChunks, parseKnowledgeAnswer, strictUuid, webRequestOptions,
  type KnowledgeAnswerResult } from './api';

export type WorkflowCreation = { requestId: string; question: string };
export type WorkflowExecution = { requestId: string; expectedVersion: 0 };
export type WorkflowResume = { requestId: string; expectedVersion: number; confirmed: true };
export type WorkflowMeta = {
  id: string; question: string; maxContextCharacters: number;
  status: 'pending' | 'running' | 'failed' | 'completed'; nextStep: 'retrieve' | 'generate' | 'done';
  version: number; attemptCount: number; checkpointCount: number | null;
  errorCode: string | null; retryable: boolean;
};
export type WorkflowReceipt = {
  requestId: string; attemptNumber: number; status: 'completed' | 'failed'; workflow: WorkflowMeta;
  answer: KnowledgeAnswerResult | null;
};
export type WorkflowSavedAnswer = { workflow: WorkflowMeta; answer: KnowledgeAnswerResult };
type Stage = 'create' | 'execute' | 'read' | 'resume' | 'answer-read';
export const WORKFLOW_NOTICES: Record<Stage, string> = {
  create: '工作流创建结果未确认；可能已保存任务，不自动再次创建。',
  execute: '工作流执行结果未确认；可能已调用外部服务或保存检查点，不自动重试或恢复。',
  read: '工作流当前状态未确认；可按需重新读取，不会自动执行。',
  resume: '生成恢复结果未确认；可能已调用生成服务或保存结果，本页不自动重试或再次恢复。',
  'answer-read': '已保存答案尚未核对通过；本次只读查看不会执行工作流或重新生成，请先重新读取当前状态。',
};
export class WorkflowUnconfirmedError extends ApiError {
  readonly stage: Stage;
  constructor(stage: Stage, status: number | null = null) {
    super(WORKFLOW_NOTICES[stage], status); this.name = 'WorkflowUnconfirmedError'; this.stage = stage;
  }
}
const ERRORS = new Set(['embedding_timeout', 'embedding_error', 'retrieval_conflict', 'retrieval_invalid',
  'generation_configuration', 'generation_timeout', 'generation_error', 'evidence_changed',
  'checkpoint_invalid', 'context_limit', 'service_error', 'execution_interrupted']);
const RETRYABLE = new Set(['embedding_timeout', 'embedding_error', 'generation_configuration',
  'generation_timeout', 'generation_error', 'service_error', 'execution_interrupted']);
type Hit = { chunkId: string; documentId: string; documentName: string; chunkIndex: number;
  content: string; startChar: number; endChar: number; distance: number;
  fusionScore: number | null; lexicalScore: number | null; vectorRank: number | null;
  lexicalRank: number | null; retrievalRank: number | null; rerankRank: number | null };
type Parsed = { meta: WorkflowMeta; hits: Hit[]; answer: KnowledgeAnswerResult | null };
// Keep verified checkpoint provenance private and in memory. Metadata GET never manufactures it.
type CheckpointProvenance = { workspace: string; knowledgeBase: string; hits: readonly Hit[] };
const receiptCheckpoints = new WeakMap<WorkflowReceipt, CheckpointProvenance & { meta: WorkflowMeta; requestId: string }>();
type ReadSnapshot = CheckpointProvenance & { meta: WorkflowMeta; answer: string | null };
const readSnapshots = new WeakMap<WorkflowMeta, ReadSnapshot>();
export function sameWorkflowMeta(left: WorkflowMeta, right: WorkflowMeta): boolean {
  return left.id === right.id && left.question === right.question && left.maxContextCharacters === right.maxContextCharacters
    && left.status === right.status && left.nextStep === right.nextStep && left.version === right.version
    && left.attemptCount === right.attemptCount && left.checkpointCount === right.checkpointCount
    && left.errorCode === right.errorCode && left.retryable === right.retryable;
}
export function canResumeWorkflow(failure: WorkflowReceipt | null, current: WorkflowMeta | null): boolean {
  if (!failure || !current) return false;
  const saved = receiptCheckpoints.get(failure);
  const snapshot = readSnapshots.get(current);
  return !!saved && !!snapshot && snapshot.workspace === saved.workspace && snapshot.knowledgeBase === saved.knowledgeBase
    && JSON.stringify(snapshot.hits) === JSON.stringify(saved.hits)
    && saved.requestId === failure.requestId && failure.status === 'failed' && failure.attemptNumber === 1
    && sameWorkflowMeta(failure.workflow, saved.meta) && sameWorkflowMeta(current, saved.meta)
    && sameWorkflowMeta(current, snapshot.meta)
    && current.status === 'failed' && current.nextStep === 'generate' && current.retryable
    && current.checkpointCount !== null && current.attemptCount === 1 && current.version <= 28;
}
export function canReadSavedWorkflowAnswer(current: WorkflowMeta | null): boolean {
  if (!current) return false;
  const snapshot = readSnapshots.get(current);
  return !!snapshot && snapshot.meta.status === 'completed' && snapshot.answer !== null
    && sameWorkflowMeta(current, snapshot.meta);
}
function invalid(): never { throw new Error('Invalid public workflow'); }
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function text(value: unknown, maximum: number, nonblank = true): string {
  if (typeof value !== 'string' || !value.length || (nonblank && !value.trim())) return invalid();
  let length = 0;
  for (const _character of value) if (++length > maximum) return invalid();
  return value;
}
function integer(value: unknown, minimum: number, maximum = Number.MAX_SAFE_INTEGER): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) return invalid();
  return value as number;
}
function finite(value: unknown, minimum: number, maximum = Number.MAX_VALUE): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum) return invalid();
  return value;
}
function path(workspace: string, knowledgeBase: string): string {
  return `/api/v1/workspaces/${strictUuid(workspace)}/knowledge-bases/${strictUuid(knowledgeBase)}/answer-workflows`;
}
async function request(url: string, stage: Stage, status: number, signal: AbortSignal, body?: unknown): Promise<unknown> {
  if (signal.aborted) throw new WorkflowUnconfirmedError(stage);
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(cancel, stage === 'execute' || stage === 'resume' ? 630_000 : 10_000);
  try {
    const options: RequestInit = body === undefined ? { method: 'GET' }
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const response = await fetch(url, webRequestOptions(options, controller.signal));
    if (controller.signal.aborted) throw new WorkflowUnconfirmedError(stage);
    const messages: Record<number, string> = {
      401: '登录已失效，请重新登录。', 403: '工作流请求被拒绝，请检查登录与浏览器来源。',
      404: '该知识库或工作流不存在，或你已无权访问，请返回文档列表。',
      409: '工作流版本、状态或执行权冲突；请手动读取状态，本页不会自动重试或恢复。',
      422: '工作流请求参数或保存状态未被接受，本页不会自动重试。',
    };
    if (Object.hasOwn(messages, response.status)) throw new ApiError(messages[response.status], response.status);
    if (response.status !== status) throw new WorkflowUnconfirmedError(stage, response.status);
    const payload: unknown = await response.json();
    if (controller.signal.aborted) throw new WorkflowUnconfirmedError(stage);
    return payload;
  } catch (failure) {
    if (controller.signal.aborted) throw new WorkflowUnconfirmedError(stage);
    if (failure instanceof ApiError) throw failure;
    throw new WorkflowUnconfirmedError(stage);
  } finally { clearTimeout(timer); signal.removeEventListener('abort', cancel); }
}

function parseCheckpoint(raw: unknown, workspace: string, knowledgeBase: string, budget: number): Hit[] {
  if (!object(raw) || raw.schema_version !== 1 || raw.workspace_id !== workspace
      || raw.knowledge_base_id !== knowledgeBase || raw.strategy !== 'vector'
      || !Array.isArray(raw.hits) || raw.hits.length > 5
      || new TextEncoder().encode(JSON.stringify(raw)).length > 100_000) return invalid();
  const chunks = new Set<string>(), documents = new Map<string, string>();
  let total = 0;
  return raw.hits.map((item: unknown): Hit => {
    if (!object(item)) return invalid();
    const chunkId = strictUuid(item.chunk_id), documentId = strictUuid(item.document_id);
    const documentName = text(item.document_name, 255);
    const content = text(item.content, 12000, false);
    const startChar = integer(item.start_char, 0), endChar = integer(item.end_char, 1);
    const length = Array.from(content).length;
    total += length;
    if (endChar - startChar !== length || total > budget || chunks.has(chunkId)
        || (documents.has(documentId) && documents.get(documentId) !== documentName)) return invalid();
    finite(item.distance, 0, 2);
    for (const field of ['fusion_score', 'lexical_score']) {
      if (item[field] !== null && item[field] !== undefined) finite(item[field], 0);
    }
    for (const field of ['vector_rank', 'lexical_rank', 'retrieval_rank', 'rerank_rank']) {
      if (item[field] !== null && item[field] !== undefined) integer(item[field], 1);
    }
    chunks.add(chunkId); documents.set(documentId, documentName);
    return { chunkId, documentId, documentName, content, chunkIndex: integer(item.chunk_index, 1), startChar, endChar,
      distance: item.distance as number, fusionScore: (item.fusion_score as number | undefined) ?? null,
      lexicalScore: (item.lexical_score as number | undefined) ?? null,
      vectorRank: (item.vector_rank as number | undefined) ?? null,
      lexicalRank: (item.lexical_rank as number | undefined) ?? null,
      retrievalRank: (item.retrieval_rank as number | undefined) ?? null,
      rerankRank: (item.rerank_rank as number | undefined) ?? null };
  });
}

function parseState(raw: unknown, workspace: string, knowledgeBase: string, question: string,
  previous?: WorkflowMeta): Parsed {
  if (!object(raw) || raw.workspace_id !== workspace || raw.knowledge_base_id !== knowledgeBase
      || raw.question !== question || raw.strategy !== 'vector') return invalid();
  const id = strictUuid(raw.workflow_id);
  const maxContextCharacters = integer(raw.max_context_characters, 1, 12000);
  const status = raw.status, nextStep = raw.next_step;
  const version = integer(raw.version, 0, 32), attemptCount = integer(raw.attempt_count, 0, 5);
  if ((previous && (id !== previous.id || maxContextCharacters !== previous.maxContextCharacters))
      || (status !== 'pending' && status !== 'running' && status !== 'failed' && status !== 'completed')
      || (nextStep !== 'retrieve' && nextStep !== 'generate' && nextStep !== 'done')) return invalid();
  const checkpoint = raw.checkpoint;
  if ((nextStep === 'retrieve') !== (checkpoint === null)) return invalid();
  const hits = checkpoint === null ? [] : parseCheckpoint(checkpoint, workspace, knowledgeBase, maxContextCharacters);
  if (status === 'running') {
    strictUuid(raw.active_request_id);
    if (typeof raw.lease_expires_at !== 'string'
        || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(raw.lease_expires_at)
        || !Number.isFinite(Date.parse(raw.lease_expires_at))) return invalid();
  } else if (raw.active_request_id !== null || raw.lease_expires_at !== null) return invalid();
  let errorCode = null;
  if (status === 'failed') {
    if (typeof raw.error_code !== 'string' || !ERRORS.has(raw.error_code)) return invalid();
    errorCode = raw.error_code;
  } else if (raw.error_code !== null) return invalid();
  const retryable = status === 'failed' && RETRYABLE.has(errorCode!) && attemptCount < 5;
  if (raw.retryable !== retryable || (status === 'pending' ? nextStep !== 'retrieve' || attemptCount !== 0 || version !== 0
    : attemptCount === 0 || version === 0)) return invalid();
  let answer = null;
  if (status === 'completed') {
    if (nextStep !== 'done' || !object(raw.result) || raw.result.reranking !== null) return invalid();
    answer = parseKnowledgeAnswer(raw.result);
    const byChunk = new Map(hits.map(hit => [hit.chunkId, hit]));
    for (const citation of answer.citations) {
      const hit = byChunk.get(citation.chunkId);
      if (!hit || citation.documentId !== hit.documentId || citation.documentName !== hit.documentName
          || citation.chunkIndex !== hit.chunkIndex || citation.startChar < hit.startChar || citation.endChar > hit.endChar
          || Array.from(hit.content).slice(citation.startChar - hit.startChar, citation.endChar - hit.startChar).join('') !== citation.quote) return invalid();
    }
  } else if (nextStep === 'done' || raw.result !== null) return invalid();
  return { meta: { id, question, maxContextCharacters, status, nextStep, version, attemptCount,
    checkpointCount: checkpoint === null ? null : hits.length, errorCode, retryable }, hits, answer };
}

export async function createAnswerWorkflow(workspaceId: string, knowledgeBaseId: string,
  creation: WorkflowCreation, signal: AbortSignal): Promise<WorkflowMeta> {
  const workspace = strictUuid(workspaceId), knowledgeBase = strictUuid(knowledgeBaseId);
  const requestId = strictUuid(creation.requestId);
  let question: string;
  try { question = text(creation.question.trim(), 2000); }
  catch { throw new ApiError('请输入去掉首尾空白后1到2000个字符的问题。'); }
  const payload = await request(path(workspace, knowledgeBase), 'create', 201, signal,
    { request_id: requestId, question, strategy: 'vector' });
  try {
    const parsed = parseState(payload, workspace, knowledgeBase, question);
    if (signal.aborted || parsed.meta.status !== 'pending') return invalid();
    return parsed.meta;
  } catch { throw new WorkflowUnconfirmedError('create'); }
}

export async function executeAnswerWorkflow(workspaceId: string, knowledgeBaseId: string,
  created: WorkflowMeta, execution: WorkflowExecution, signal: AbortSignal): Promise<WorkflowReceipt> {
  const workspace = strictUuid(workspaceId), knowledgeBase = strictUuid(knowledgeBaseId);
  const id = strictUuid(created.id), requestId = strictUuid(execution.requestId);
  if (created.status !== 'pending' || created.version !== 0 || created.attemptCount !== 0 || execution.expectedVersion !== 0) {
    throw new ApiError('本页只执行已确认新建的初始任务一次，不提供重试或恢复。');
  }
  const payload = await request(`${path(workspace, knowledgeBase)}/${id}/execute`, 'execute', 200, signal,
    { request_id: requestId, expected_version: 0, recover_interrupted: false });
  return confirmExecution(payload, workspace, knowledgeBase, created, requestId, signal, 'execute');
}

async function confirmExecution(payload: unknown, workspace: string, knowledgeBase: string, previous: WorkflowMeta,
  requestId: string, signal: AbortSignal, stage: 'execute' | 'resume', originalHits?: readonly Hit[]): Promise<WorkflowReceipt> {
  try {
    const expectedAttempt = stage === 'execute' ? 1 : previous.attemptCount + 1;
    if (signal.aborted || !object(payload) || payload.request_id !== requestId || payload.attempt_number !== expectedAttempt
        || (payload.status !== 'completed' && payload.status !== 'failed')) return invalid();
    const parsed = parseState(payload.workflow, workspace, knowledgeBase, previous.question, previous);
    const meta = parsed.meta;
    if (meta.attemptCount !== expectedAttempt || meta.status !== payload.status) return invalid();
    if (stage === 'execute' && (
        (meta.status === 'completed' ? meta.version !== 3 : meta.version !== 2 && meta.version !== 3)
        || (meta.version === 2 && meta.checkpointCount !== null)
        || (meta.status === 'failed' && meta.nextStep === 'generate' && meta.version !== 3)
        || (meta.status === 'failed' && meta.nextStep === 'retrieve' && meta.version === 3
          && meta.errorCode !== 'evidence_changed' && meta.errorCode !== 'checkpoint_invalid'))) return invalid();
    if (stage === 'resume' && (meta.version !== previous.version + 2
        || (meta.checkpointCount === null
          ? meta.status !== 'failed' || (meta.errorCode !== 'evidence_changed' && meta.errorCode !== 'checkpoint_invalid')
          : JSON.stringify(parsed.hits) !== JSON.stringify(originalHits)))) return invalid();
    for (const [documentId, documentName] of new Map(parsed.hits.map(hit => [hit.documentId, hit.documentName]))) {
      if (signal.aborted) return invalid();
      const document = await getDocument(workspace, knowledgeBase, documentId, signal);
      if (signal.aborted || document.originalFilename !== documentName || document.status !== 'ready') return invalid();
    }
    if (signal.aborted) return invalid();
    const receipt: WorkflowReceipt = { requestId, attemptNumber: expectedAttempt, status: payload.status, workflow: meta, answer: parsed.answer };
    receiptCheckpoints.set(receipt, { meta: { ...meta }, hits: parsed.hits, requestId, workspace, knowledgeBase });
    return receipt;
  } catch (failure) {
    if (!signal.aborted && failure instanceof ApiError && failure.status === 401) throw failure;
    throw new WorkflowUnconfirmedError(stage, failure instanceof ApiError ? failure.status : null);
  }
}

export async function resumeAnswerWorkflow(workspaceId: string, knowledgeBaseId: string,
  failure: WorkflowReceipt, current: WorkflowMeta, submitted: WorkflowResume, signal: AbortSignal): Promise<WorkflowReceipt> {
  const workspace = strictUuid(workspaceId), knowledgeBase = strictUuid(knowledgeBaseId);
  const requestId = strictUuid(submitted.requestId);
  if (!canResumeWorkflow(failure, current) || submitted.confirmed !== true
      || submitted.expectedVersion !== current.version || requestId === failure.requestId) {
    throw new ApiError('请先核对本页已确认的生成失败状态，再明确允许新的调用；本步不恢复未知结果或执行中任务。');
  }
  const saved = receiptCheckpoints.get(failure)!;
  if (saved.workspace !== workspace || saved.knowledgeBase !== knowledgeBase) {
    throw new ApiError('恢复范围与已确认任务不一致。');
  }
  const payload = await request(`${path(workspace, knowledgeBase)}/${strictUuid(current.id)}/execute`, 'resume', 200, signal,
    { request_id: requestId, expected_version: current.version, recover_interrupted: false });
  return confirmExecution(payload, workspace, knowledgeBase, current, requestId, signal, 'resume', saved.hits);
}

export async function readAnswerWorkflow(workspaceId: string, knowledgeBaseId: string,
  created: WorkflowMeta, signal: AbortSignal): Promise<WorkflowMeta> {
  const workspace = strictUuid(workspaceId), knowledgeBase = strictUuid(knowledgeBaseId);
  const payload = await request(`${path(workspace, knowledgeBase)}/${strictUuid(created.id)}`, 'read', 200, signal);
  try {
    if (signal.aborted) return invalid();
    // Deliberately project metadata only: GET cannot confirm this page's execution request.
    const parsed = parseState(payload, workspace, knowledgeBase, created.question, created);
    // No public object aliases these immutable copies. A later metadata mutation cannot grant access.
    readSnapshots.set(parsed.meta, { workspace, knowledgeBase, meta: Object.freeze({ ...parsed.meta }),
      hits: Object.freeze(parsed.hits.map(hit => Object.freeze({ ...hit }))),
      answer: parsed.answer === null ? null : JSON.stringify(parsed.answer) });
    return parsed.meta;
  } catch { throw new WorkflowUnconfirmedError('read'); }
}

export async function readSavedWorkflowAnswer(workspaceId: string, knowledgeBaseId: string,
  created: WorkflowMeta, current: WorkflowMeta, signal: AbortSignal): Promise<WorkflowSavedAnswer> {
  const workspace = strictUuid(workspaceId), knowledgeBase = strictUuid(knowledgeBaseId);
  const snapshot = readSnapshots.get(current);
  const matchesCreation = () => snapshot && created.id === snapshot.meta.id
    && created.question === snapshot.meta.question && created.maxContextCharacters === snapshot.meta.maxContextCharacters;
  if (!canReadSavedWorkflowAnswer(current) || !snapshot || snapshot.workspace !== workspace
      || snapshot.knowledgeBase !== knowledgeBase || !matchesCreation()) {
    throw new ApiError('请先手动读取本页任务的当前状态，确认已完成后再查看已保存答案。');
  }
  const payload = await request(`${path(workspace, knowledgeBase)}/${snapshot.meta.id}`, 'answer-read', 200, signal);
  try {
    if (signal.aborted || !sameWorkflowMeta(current, snapshot.meta) || !matchesCreation()) return invalid();
    const parsed = parseState(payload, workspace, knowledgeBase, snapshot.meta.question, snapshot.meta);
    if (!sameWorkflowMeta(parsed.meta, snapshot.meta) || parsed.answer === null
        || JSON.stringify(parsed.hits) !== JSON.stringify(snapshot.hits)
        || JSON.stringify(parsed.answer) !== snapshot.answer) return invalid();
    for (const [documentId, documentName] of new Map(parsed.hits.map(hit => [hit.documentId, hit.documentName]))) {
      if (signal.aborted) return invalid();
      const document = await getDocument(workspace, knowledgeBase, documentId, signal);
      if (signal.aborted || document.originalFilename !== documentName || document.status !== 'ready') return invalid();
      const chunks = await listDocumentChunks(workspace, knowledgeBase, documentId, signal);
      if (signal.aborted) return invalid();
      const byId = new Map(chunks.map(chunk => [chunk.id, chunk]));
      for (const hit of parsed.hits.filter(hit => hit.documentId === documentId)) {
        const chunk = byId.get(hit.chunkId);
        if (!chunk || chunk.documentId !== hit.documentId || chunk.chunkIndex !== hit.chunkIndex
            || chunk.content !== hit.content || chunk.startChar !== hit.startChar || chunk.endChar !== hit.endChar) return invalid();
      }
    }
    if (signal.aborted || !sameWorkflowMeta(current, snapshot.meta) || !matchesCreation()) return invalid();
    // This GET projection is not an execution receipt and grants no write or resume permission.
    return { workflow: parsed.meta, answer: parsed.answer };
  } catch (failure) {
    if (!signal.aborted && failure instanceof ApiError && (failure.status === 401 || failure.status === 404)) throw failure;
    throw new WorkflowUnconfirmedError('answer-read', failure instanceof ApiError ? failure.status : null);
  }
}
