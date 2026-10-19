import { ApiError, responseTraceId, strictUuid, webRequestOptions } from './api';
import type { WorkflowMeta, WorkflowReadTarget } from './workflow-api';

export type WorkflowHistoryItem = WorkflowReadTarget & {
  strategy: 'vector' | 'hybrid';
  status: WorkflowMeta['status']; nextStep: WorkflowMeta['nextStep'];
  version: number; attemptCount: number; errorCode: string | null; retryable: boolean;
  createdAt: string; updatedAt: string;
};

const UNCONFIRMED = '工作流记录尚未读取确认；可手动重新读取，本次只读操作不会执行或恢复任务。';
const ERRORS = new Set(['embedding_timeout', 'embedding_error', 'retrieval_conflict', 'retrieval_invalid',
  'generation_configuration', 'generation_timeout', 'generation_error', 'evidence_changed',
  'checkpoint_invalid', 'context_limit', 'service_error', 'execution_interrupted']);
const RETRYABLE = new Set(['embedding_timeout', 'embedding_error', 'generation_configuration',
  'generation_timeout', 'generation_error', 'service_error', 'execution_interrupted']);

function invalid(): never { throw new ApiError(UNCONFIRMED); }
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function integer(value: unknown, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) return invalid();
  return value as number;
}

// Compare complete instants, including the backend's microseconds, rather than truncating to milliseconds.
function timestamp(value: unknown): { text: string; instant: bigint } {
  if (typeof value !== 'string' || value.length > 32) return invalid();
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!parts) return invalid();
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, fraction, zone] = parts;
  const year = Number(yearText), month = Number(monthText), day = Number(dayText);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1]
      || Number(hourText) > 23 || Number(minuteText) > 59 || Number(secondText) > 59
      || (zone !== 'Z' && (Number(zone.slice(1, 3)) > 23 || Number(zone.slice(4)) > 59))) return invalid();
  const milliseconds = Date.parse(value);
  if (!Number.isSafeInteger(milliseconds)) return invalid();
  return { text: value, instant: BigInt(milliseconds) * 1000n + BigInt((fraction ?? '').padEnd(6, '0').slice(3)) };
}

function parseList(payload: unknown, workspace: string, knowledgeBase: string): WorkflowHistoryItem[] {
  if (!Array.isArray(payload) || payload.length > 20) return invalid();
  const ids = new Set<string>();
  let previous: { id: string; created: bigint } | null = null;
  return payload.map((raw: unknown) => {
    if (!object(raw) || strictUuid(raw.workspace_id) !== workspace || strictUuid(raw.knowledge_base_id) !== knowledgeBase) return invalid();
    const id = strictUuid(raw.workflow_id);
    if (ids.has(id)) return invalid();
    if (typeof raw.question !== 'string' || !raw.question.trim()
        || Array.from(raw.question).length > 2000 || (raw.strategy !== 'vector' && raw.strategy !== 'hybrid')) return invalid();
    const maxContextCharacters = integer(raw.max_context_characters, 1, 12000);
    const version = integer(raw.version, 0, 32), attemptCount = integer(raw.attempt_count, 0, 5);
    const status = raw.status, nextStep = raw.next_step;
    if ((status !== 'pending' && status !== 'running' && status !== 'failed' && status !== 'completed')
        || (nextStep !== 'retrieve' && nextStep !== 'generate' && nextStep !== 'done')
        || ((status === 'completed') !== (nextStep === 'done'))
        || (status === 'pending' ? nextStep !== 'retrieve' || attemptCount !== 0 || version !== 0
          : attemptCount === 0 || version === 0)) return invalid();
    let errorCode: string | null = null;
    if (status === 'failed') {
      if (typeof raw.error_code !== 'string' || !ERRORS.has(raw.error_code)) return invalid();
      errorCode = raw.error_code;
    } else if (raw.error_code !== null) return invalid();
    const retryable = status === 'failed' && RETRYABLE.has(errorCode!) && attemptCount < 5;
    if (raw.retryable !== retryable) return invalid();
    const created = timestamp(raw.created_at), updated = timestamp(raw.updated_at);
    if (created.instant > updated.instant || (previous && (created.instant > previous.created
        || (created.instant === previous.created && id >= previous.id)))) return invalid();
    ids.add(id); previous = { id, created: created.instant };
    // Project only summary fields: a list row contains neither checkpoint/result nor any execution receipt.
    return { id, question: raw.question, maxContextCharacters, strategy: raw.strategy, status, nextStep,
      version, attemptCount, errorCode, retryable, createdAt: created.text, updatedAt: updated.text };
  });
}

export async function listAnswerWorkflows(workspaceId: string, knowledgeBaseId: string,
  signal: AbortSignal): Promise<WorkflowHistoryItem[]> {
  const workspace = strictUuid(workspaceId), knowledgeBase = strictUuid(knowledgeBaseId);
  if (signal.aborted) return invalid();
  const controller = new AbortController(), cancel = () => controller.abort();
  signal.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(cancel, 10_000);
  try {
    const url = `/api/v1/workspaces/${workspace}/knowledge-bases/${knowledgeBase}/answer-workflows?limit=20&offset=0`;
    const response = await fetch(url, webRequestOptions({ method: 'GET' }, controller.signal));
    if (controller.signal.aborted) return invalid();
    const messages: Record<number, string> = {
      401: '登录已失效，请重新登录。', 403: '工作流记录请求被拒绝，请检查登录与浏览器来源。',
      404: '该知识库不存在，或你已无权访问，请返回文档列表。',
      422: '工作流记录请求参数或保存状态未被接受，请手动重新读取。',
    };
    if (Object.hasOwn(messages, response.status)) {
      throw new ApiError(messages[response.status], response.status, responseTraceId(response));
    }
    if (response.status !== 200) throw new ApiError(UNCONFIRMED, response.status, responseTraceId(response));
    const payload: unknown = await response.json();
    if (controller.signal.aborted) return invalid();
    return parseList(payload, workspace, knowledgeBase);
  } catch (failure) {
    if (controller.signal.aborted) return invalid();
    if (failure instanceof ApiError) throw failure;
    return invalid();
  } finally { clearTimeout(timer); signal.removeEventListener('abort', cancel); }
}
