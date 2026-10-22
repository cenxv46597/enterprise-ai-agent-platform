import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { isWorkflowHistoryOffset, isWorkflowHistoryStatusFilter, listAnswerWorkflows,
  WORKFLOW_HISTORY_PAGE_SIZE, type WorkflowHistoryStatusFilter } from './workflow-history-api';
import { canReadSavedWorkflowAnswer, canResumeWorkflow, readAnswerWorkflow,
  type WorkflowMeta, type WorkflowReadTarget } from './workflow-api';
import { KNOWLEDGE_BASE_ID, OTHER_KNOWLEDGE_BASE_ID, PRIVATE, QUESTION, WORKSPACE_ID,
  jsonResponse } from './conversation-test-fixtures';
import { WORKFLOW_ID, WORKFLOW_PATH, WORKFLOWS_PATH, completedWorkflow, createdWorkflow,
  failedWorkflow, runningWorkflow } from './workflow-test-fixtures';

const fetchMock = vi.fn<typeof fetch>();
const signal = () => new AbortController().signal;
const LIST_PATH = `${WORKFLOWS_PATH}?limit=20&offset=0`;
const OTHER_ID = '66666666-6666-6666-6666-666666666666';
function summary(state = createdWorkflow()) {
  const { checkpoint: _checkpoint, result: _result, active_request_id: _request, lease_expires_at: _lease, ...rest } = state;
  return { ...rest, created_at: '2026-10-18T10:00:00.123456+08:00', updated_at: '2026-10-18T10:00:01+08:00' };
}
const list = (parent = signal(), offset?: number) => listAnswerWorkflows(WORKSPACE_ID, KNOWLEDGE_BASE_ID, parent, offset);
async function rejected(action: Promise<unknown>) {
  const error = await action.catch(error => error);
  expect(error).toBeInstanceOf(ApiError); expect(String(error)).not.toContain(PRIVATE);
  return error as ApiError;
}

beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('工作流历史列表的只读摘要契约', () => {
  it('仅GET当前范围最近20条，不发送请求UUID/正文/执行请求，投影掉所有私有附加字段', async () => {
    const raw = { ...summary(completedWorkflow()), checkpoint: { hits: [{ content: PRIVATE }] },
      result: { answer: PRIVATE }, request_id: PRIVATE, active_request_id: PRIVATE,
      lease_expires_at: PRIVATE, internal_error: PRIVATE };
    fetchMock.mockResolvedValueOnce(jsonResponse([raw]));
    const records = await list();
    expect(records).toEqual([{ id: WORKFLOW_ID, question: QUESTION, strategy: 'vector', maxContextCharacters: 12000,
      status: 'completed', nextStep: 'done', version: 3, attemptCount: 1, errorCode: null, retryable: false,
      createdAt: raw.created_at, updatedAt: raw.updated_at }]);
    expect(JSON.stringify(records)).not.toContain(PRIVATE);
    for (const field of ['checkpoint', 'checkpointCount', 'answer', 'result', 'requestId', 'activeRequestId', 'leaseExpiresAt']) {
      expect(records[0]).not.toHaveProperty(field);
    }
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(LIST_PATH);
    expect(options).toMatchObject({ method: 'GET', mode: 'same-origin', credentials: 'same-origin',
      redirect: 'error', cache: 'no-store' });
    expect(options?.body).toBeUndefined();
    const headers = new Headers(options?.headers);
    expect(headers.get('X-EAAP-Web')).toBe('1');
    expect(headers.has('Authorization')).toBe(false); expect(headers.has('Cookie')).toBe(false);
    expect(headers.has('Content-Type')).toBe(false);
  });

  it('空列表合法；pending/running/failed/completed及hybrid摘要均保留真实状态', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([])); expect(await list()).toEqual([]);
    for (const state of [createdWorkflow(), runningWorkflow(), runningWorkflow('generate'),
      failedWorkflow(), failedWorkflow('generate'), completedWorkflow()]) {
      const row = summary(state); row.strategy = 'hybrid';
      fetchMock.mockResolvedValueOnce(jsonResponse([row]));
      expect((await list())[0]).toMatchObject({ strategy: 'hybrid', status: state.status, nextStep: state.next_step,
        version: state.version, attemptCount: state.attempt_count, errorCode: state.error_code, retryable: state.retryable });
    }
    expect(fetchMock.mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true);
  });

  it('接纳20条和2000个Unicode码点问题；拒绝超20条、非数组或重复规范化UUID', async () => {
    const twenty = Array.from({ length: 20 }, (_, index) => ({ ...summary(),
      workflow_id: `77777777-7777-7777-7777-${(20 - index).toString(16).padStart(12, '0')}`,
      question: '🙂'.repeat(2000) }));
    fetchMock.mockResolvedValueOnce(jsonResponse(twenty)); expect(await list()).toHaveLength(20);
    for (const payload of [null, {}, { items: [] }, [...twenty, summary()],
      [summary(), { ...summary(), workflow_id: WORKFLOW_ID.toUpperCase() }]]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload)); await rejected(list());
    }
  });

  it.each(['workspace', 'knowledge-base', 'id-newline', 'id-invalid', 'question-empty', 'question-blank',
    'question-long', 'question-number', 'strategy', 'budget-zero', 'budget-high', 'budget-fraction', 'budget-boolean',
    'version-low', 'version-high', 'version-fraction', 'version-boolean', 'attempt-low', 'attempt-high',
    'attempt-fraction', 'attempt-boolean', 'status', 'step', 'unknown-error', 'nonfailed-error', 'retryable-string'])(
    '无效摘要%s整体拒绝，不泄露原始detail或继续读取详情', async field => {
      const row: Record<string, unknown> = summary();
      switch (field) {
        case 'workspace': row.workspace_id = OTHER_ID; break;
        case 'knowledge-base': row.knowledge_base_id = OTHER_KNOWLEDGE_BASE_ID; break;
        case 'id-newline': row.workflow_id = `${WORKFLOW_ID}\n`; break;
        case 'id-invalid': row.workflow_id = PRIVATE; break;
        case 'question-empty': row.question = ''; break;
        case 'question-blank': row.question = '\n\t '; break;
        case 'question-long': row.question = '🙂'.repeat(2001); break;
        case 'question-number': row.question = 1; break;
        case 'strategy': row.strategy = 'lexical'; break;
        case 'budget-zero': row.max_context_characters = 0; break;
        case 'budget-high': row.max_context_characters = 12001; break;
        case 'budget-fraction': row.max_context_characters = 1.2; break;
        case 'budget-boolean': row.max_context_characters = true; break;
        case 'version-low': row.version = -1; break;
        case 'version-high': row.version = 33; break;
        case 'version-fraction': row.version = 0.2; break;
        case 'version-boolean': row.version = false; break;
        case 'attempt-low': row.attempt_count = -1; break;
        case 'attempt-high': row.attempt_count = 6; break;
        case 'attempt-fraction': row.attempt_count = 0.2; break;
        case 'attempt-boolean': row.attempt_count = false; break;
        case 'status': row.status = PRIVATE; break;
        case 'step': row.next_step = PRIVATE; break;
        case 'unknown-error': Object.assign(row, summary(failedWorkflow()), { error_code: PRIVATE }); break;
        case 'nonfailed-error': row.error_code = 'service_error'; break;
        case 'retryable-string': row.retryable = 'false'; break;
      }
      fetchMock.mockResolvedValueOnce(jsonResponse([row])); await rejected(list());
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

  it.each([
    { version: 1 }, { attempt_count: 1 }, { next_step: 'generate' }, { next_step: 'done' }, { retryable: true },
    { status: 'running', version: 1 }, { status: 'failed', version: 1, attempt_count: 1 },
    { status: 'completed', version: 1, attempt_count: 1 },
  ])('拒绝互相矛盾的初始摘要%j', async changes => {
    fetchMock.mockResolvedValueOnce(jsonResponse([{ ...summary(), ...changes }])); await rejected(list());
  });

  it('失败重试标记按白名单和尝试上限核对；completed/done与非初始版本必须一致', async () => {
    const failed = summary(failedWorkflow());
    for (const row of [{ ...failed, retryable: false }, { ...failed, error_code: 'evidence_changed' },
      { ...failed, attempt_count: 5 }, { ...failed, next_step: 'done' }, { ...failed, version: 0 },
      { ...summary(runningWorkflow()), next_step: 'done' }, { ...summary(completedWorkflow()), next_step: 'generate' }]) {
      fetchMock.mockResolvedValueOnce(jsonResponse([row])); await rejected(list());
    }
    for (const row of [{ ...failed, attempt_count: 5, retryable: false },
      { ...failed, error_code: 'evidence_changed', retryable: false }]) {
      fetchMock.mockResolvedValueOnce(jsonResponse([row])); expect(await list()).toHaveLength(1);
    }
  });

  it('严格按创建时刻和UUID倒序检查，不用更新时间排序，保留微秒精度及不同时区同一时刻', async () => {
    const newest = { ...summary(), workflow_id: OTHER_ID, created_at: '2026-10-18T10:00:00.123457+08:00' };
    const older = summary();
    fetchMock.mockResolvedValueOnce(jsonResponse([newest, older]));
    expect((await list()).map(row => row.id)).toEqual([OTHER_ID, WORKFLOW_ID]);
    fetchMock.mockResolvedValueOnce(jsonResponse([older, newest])); await rejected(list());
    const same = { ...older, workflow_id: OTHER_ID, created_at: '2026-10-18T02:00:00.123456Z',
      updated_at: '2026-10-18T02:00:02Z' };
    fetchMock.mockResolvedValueOnce(jsonResponse([older, same])); expect(await list()).toHaveLength(2);
    fetchMock.mockResolvedValueOnce(jsonResponse([same, older])); await rejected(list());
  });

  it.each(['2026-10-18T10:00:00', '2026-02-30T10:00:00Z', '2025-02-29T10:00:00Z',
    '0000-01-01T00:00:00Z', '2026-00-18T10:00:00Z', '2026-13-18T10:00:00Z',
    '2026-10-00T10:00:00Z', '2026-10-18T24:00:00Z', '2026-10-18T10:60:00Z',
    '2026-10-18T10:00:60Z', '2026-10-18T10:00:00+24:00', '2026-10-18T10:00:00+08:60',
    '2026-10-18T10:00:00.1234567Z', '2026-10-18T10:00:00Z\n', PRIVATE])(
    '拒绝无时区、非法日历或超精度时间%s', async createdAt => {
      fetchMock.mockResolvedValueOnce(jsonResponse([{ ...summary(), created_at: createdAt }])); await rejected(list());
    });

  it('更新时间不能早于创建时间；合法闰日和最小年份时区日期可解析', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([{ ...summary(), updated_at: '2026-10-18T10:00:00.123455+08:00' }]));
    await rejected(list());
    for (const createdAt of ['2024-02-29T23:59:59.123456Z', '0001-01-01T00:00:00Z']) {
      fetchMock.mockResolvedValueOnce(jsonResponse([{ ...summary(), created_at: createdAt }]));
      expect((await list())[0].createdAt).toBe(createdAt);
    }
  });

  it('历史摘要是只读目标，无需伪造pending任务；详情GET后才授予单独查看已保存答案的只读依据', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([summary(completedWorkflow())]));
    const [item] = await list();
    const target: WorkflowReadTarget = item;
    // @ts-expect-error A summary deliberately lacks checkpointCount and cannot stand in for a created full task.
    const _executionMetadata: WorkflowMeta = item;
    expect(canReadSavedWorkflowAnswer(item as unknown as WorkflowMeta)).toBe(false);
    expect(canResumeWorkflow(null, item as unknown as WorkflowMeta)).toBe(false);
    fetchMock.mockResolvedValueOnce(jsonResponse(completedWorkflow()));
    const current = await readAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID, target, signal());
    expect(current).toMatchObject({ status: 'completed', checkpointCount: 2 });
    expect(canReadSavedWorkflowAnswer(current)).toBe(true);
    expect(canResumeWorkflow(null, current)).toBe(false);
    expect(current).not.toHaveProperty('answer'); expect(current).not.toHaveProperty('requestId');
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([LIST_PATH, WORKFLOW_PATH]);
    expect(fetchMock.mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true);
  });

  it.each([401, 403, 404, 422])('HTTP%s只返回固定公开错误，不解析原始detail且不重试', async status => {
    const response = jsonResponse({ detail: PRIVATE }, status), json = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    expect(await rejected(list())).toMatchObject({ status });
    expect(json).not.toHaveBeenCalled(); expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([201, 202, 409, 429, 500, 502, 503, 504])('未知HTTP%s保持读取未确认，无写请求或自动重试', async status => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, status));
    expect(await rejected(list())).toMatchObject({ status }); expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('坏JSON及网络错误不泄露原始数据，不读取详情', async () => {
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE)); await rejected(list());
    fetchMock.mockRejectedValueOnce(new TypeError(PRIVATE)); await rejected(list());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('非法范围UUID在本地拒绝；预先取消不发请求', async () => {
    for (const [workspace, knowledgeBase] of [[`${WORKSPACE_ID}\n`, KNOWLEDGE_BASE_ID], [WORKSPACE_ID, '../outside']]) {
      await rejected(listAnswerWorkflows(workspace, knowledgeBase, signal()));
    }
    const controller = new AbortController(); controller.abort(); await rejected(list(controller.signal));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('10秒超时中止，迟到列表无结果，计时器及父级监听清理', async () => {
    vi.useFakeTimers(); const controller = new AbortController();
    const remove = vi.spyOn(controller.signal, 'removeEventListener');
    let resolve!: (response: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const action = list(controller.signal).catch(error => error);
    await vi.advanceTimersByTimeAsync(9999); expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1); expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    resolve(jsonResponse([summary()])); expect(await action).toBeInstanceOf(ApiError);
    expect(vi.getTimerCount()).toBe(0); expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(['fetch', 'json'] as const)('父级取消%s期间的读取后，迟到结果不可返回或继续请求', async stage => {
    const controller = new AbortController();
    let resolve!: (value: never) => void;
    const deferred = new Promise<never>(done => { resolve = done; });
    const response = jsonResponse([summary()]);
    if (stage === 'fetch') fetchMock.mockReturnValueOnce(deferred);
    else { vi.spyOn(response, 'json').mockReturnValueOnce(deferred); fetchMock.mockResolvedValueOnce(response); }
    const action = list(controller.signal).catch(error => error);
    await Promise.resolve(); controller.abort();
    resolve((stage === 'fetch' ? response : [summary()]) as never);
    expect(await action).toBeInstanceOf(ApiError);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true); expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

const TRACE_A = 'a0e0b901-2b0d-4ae0-812a-bab2cf9e7b01';
const TRACE_B = 'b1f1c012-3c1e-4bf1-923b-cbc3dfa08c12';
function tracedResponse(payload: unknown, status: number, trace?: string) {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (trace !== undefined) headers.set('X-Trace-Id', trace);
  return new Response(JSON.stringify(payload), { status, headers });
}

const FILTER_STATES = ['pending', 'running', 'failed', 'completed'] as const;
function filterState(status: typeof FILTER_STATES[number]) {
  return status === 'pending' ? createdWorkflow() : status === 'running' ? runningWorkflow()
    : status === 'failed' ? failedWorkflow() : completedWorkflow();
}
function filteredList(statusFilter: WorkflowHistoryStatusFilter = 'all', offset = 0, parent = signal()) {
  return listAnswerWorkflows(WORKSPACE_ID, KNOWLEDGE_BASE_ID, parent, offset, statusFilter);
}

describe('工作流历史状态条件与后端分页的只读契约', () => {
  it.each(['all', ...FILTER_STATES])('只接纳精确白名单条件%s', statusFilter => {
    expect(isWorkflowHistoryStatusFilter(statusFilter)).toBe(true);
  });

  it.each([undefined, 'all'] as const)('默认或显式%s保持旧URL，不发送status=all', async statusFilter => {
    fetchMock.mockResolvedValueOnce(jsonResponse([summary()]));
    const records = await listAnswerWorkflows(WORKSPACE_ID, KNOWLEDGE_BASE_ID, signal(), 20, statusFilter);
    expect(records).toHaveLength(1);
    expect(fetchMock.mock.calls[0][0]).toBe(`${WORKFLOWS_PATH}?limit=20&offset=20`);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('全部状态允许同一页混合四种合法状态，仍投影掉执行内容', async () => {
    const rows = FILTER_STATES.map((status, index) => ({ ...summary(filterState(status)),
      workflow_id: `11111111-1111-4111-8111-${String(4 - index).padStart(12, '0')}`,
      checkpoint: PRIVATE, result: PRIVATE }));
    fetchMock.mockResolvedValueOnce(jsonResponse(rows));
    const records = await filteredList();
    expect(records.map(row => row.status)).toEqual(FILTER_STATES);
    expect(fetchMock.mock.calls[0][0]).toBe(LIST_PATH);
    expect(JSON.stringify(records)).not.toContain(PRIVATE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(FILTER_STATES)('%s与offset20携带到同一只读GET，不拆成全部列表和本地筛选', async statusFilter => {
    const row = { ...summary(filterState(statusFilter)), strategy: 'hybrid', checkpoint: PRIVATE, result: PRIVATE };
    fetchMock.mockResolvedValueOnce(jsonResponse([row]));
    const records = await filteredList(statusFilter, 20);
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({ status: statusFilter, strategy: 'hybrid' });
    expect(JSON.stringify(records)).not.toContain(PRIVATE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(`${WORKFLOWS_PATH}?limit=20&offset=20&status=${statusFilter}`);
    expect(options).toMatchObject({ method: 'GET', mode: 'same-origin', credentials: 'same-origin',
      cache: 'no-store', redirect: 'error' });
    expect(options?.body).toBeUndefined();
    const headers = new Headers(options?.headers);
    expect(headers.get('X-EAAP-Web')).toBe('1');
    expect(headers.has('Authorization')).toBe(false); expect(headers.has('Cookie')).toBe(false);
    expect(headers.has('X-Trace-Id')).toBe(false);
  });

  it.each(['', 'ALL', 'Pending', ' completed', 'completed ', 'failed\n', '*', 'status=failed',
    'completed&offset=0', 'pending%00', null, true, false, 0, 1, {}, [], ['pending']]
    .map(statusFilter => ({ statusFilter })))('非法条件$statusFilter在HTTP前拒绝，不转换或拼入URL', async ({ statusFilter }) => {
    expect(isWorkflowHistoryStatusFilter(statusFilter)).toBe(false);
    expect(await rejected(filteredList(statusFilter as WorkflowHistoryStatusFilter)))
      .toMatchObject({ status: null, traceId: null });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(FILTER_STATES)('%s条件收到空页合法，不额外读取全部状态或下一页', async statusFilter => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    expect(await filteredList(statusFilter, 40)).toEqual([]);
    expect(fetchMock.mock.calls[0][0]).toBe(`${WORKFLOWS_PATH}?limit=20&offset=40&status=${statusFilter}`);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(FILTER_STATES)('%s响应含一个合法但不匹配的状态时整页拒绝，不做本地删行或沿用成功编号', async statusFilter => {
    const otherStatus = FILTER_STATES[(FILTER_STATES.indexOf(statusFilter) + 1) % FILTER_STATES.length];
    const matching = summary(filterState(statusFilter));
    const mismatch = { ...summary(filterState(otherStatus)), workflow_id: OTHER_ID };
    for (const payload of [[mismatch], [matching, mismatch]]) {
      fetchMock.mockResolvedValueOnce(tracedResponse(payload, 200, TRACE_A));
      expect(await rejected(filteredList(statusFilter, 20))).toMatchObject({ status: null, traceId: null });
    }
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.every(([url]) => url === `${WORKFLOWS_PATH}?limit=20&offset=20&status=${statusFilter}`)).toBe(true);
  });

  it('匹配状态仍须核对知识库归属；状态筛选不能绕过原摘要契约', async () => {
    fetchMock.mockResolvedValueOnce(tracedResponse([
      { ...summary(failedWorkflow()), knowledge_base_id: OTHER_KNOWLEDGE_BASE_ID },
    ], 200, TRACE_A));
    expect(await rejected(filteredList('failed'))).toMatchObject({ status: null, traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('带条件的失败只保留当前HTTP编号，不解析错误正文、回退全部状态或自动重试', async () => {
    const response = tracedResponse({ detail: PRIVATE, trace_id: TRACE_B }, 503, TRACE_A);
    const json = vi.spyOn(response, 'json'); fetchMock.mockResolvedValueOnce(response);
    expect(await rejected(filteredList('failed', 20))).toMatchObject({ status: 503, traceId: TRACE_A });
    expect(json).not.toHaveBeenCalled(); expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(`${WORKFLOWS_PATH}?limit=20&offset=20&status=failed`);
  });
});

describe('工作流历史固定20条的分页偏移', () => {
  it.each([0, 20, 40, 9_007_199_254_740_980])('偏移%s只读取指定一页，安全边界不取整或截断', async offset => {
    expect(WORKFLOW_HISTORY_PAGE_SIZE).toBe(20);
    expect(isWorkflowHistoryOffset(offset)).toBe(true);
    fetchMock.mockResolvedValueOnce(jsonResponse([summary()]));
    expect(await list(signal(), offset)).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(`${WORKFLOWS_PATH}?limit=20&offset=${offset}`);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'GET', cache: 'no-store' });
    expect(fetchMock.mock.calls[0][1]?.body).toBeUndefined();
  });

  it.each([-20, -1, 1, 19, 21, 20.5, '20', '0', null, true, false,
    NaN, Infinity, -Infinity, Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER + 1,
    9_007_199_254_741_000, {}, [], 20n].map(offset => ({ offset })))(
    '非法偏移$offset在发请求前拒绝，不做隐式转换', async ({ offset }) => {
    expect(isWorkflowHistoryOffset(offset)).toBe(false);
    expect(await rejected(list(signal(), offset as number))).toMatchObject({ status: null, traceId: null });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('第二页仍整页核对归属、数量、重复与排序，不借用成功响应编号', async () => {
    const newer = { ...summary(), workflow_id: OTHER_ID, created_at: '2026-10-18T10:00:00.123457+08:00' };
    const payloads = [[{ ...summary(), knowledge_base_id: OTHER_KNOWLEDGE_BASE_ID }],
      Array.from({ length: 21 }, () => summary()), [summary(), summary()], [summary(), newer]];
    for (const payload of payloads) {
      fetchMock.mockResolvedValueOnce(tracedResponse(payload, 200, TRACE_A));
      expect(await rejected(list(signal(), 20))).toMatchObject({ status: null, traceId: null });
    }
    expect(fetchMock).toHaveBeenCalledTimes(payloads.length);
    expect(fetchMock.mock.calls.every(([url, options]) => url === `${WORKFLOWS_PATH}?limit=20&offset=20`
      && options?.method === 'GET')).toBe(true);
  });

  it('首请求成功后第二页HTTP失败仍只携带本次失败编号，不读正文或重试', async () => {
    fetchMock.mockResolvedValueOnce(tracedResponse([summary()], 200, TRACE_A));
    await list();
    const response = tracedResponse({ detail: PRIVATE, trace_id: TRACE_A }, 503, TRACE_B);
    const body = vi.spyOn(response, 'json'); fetchMock.mockResolvedValueOnce(response);
    expect(await rejected(list(signal(), 20))).toMatchObject({ status: 503, traceId: TRACE_B });
    expect(body).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([LIST_PATH, `${WORKFLOWS_PATH}?limit=20&offset=20`]);
    expect(fetchMock.mock.calls.every(([, options]) => options?.method === 'GET'
      && !new Headers(options?.headers).has('X-Trace-Id'))).toBe(true);
  });

  it.each(['cancel', 'timeout'])('第二页%s后迟到的错误编号不恢复，也不重试', async mode => {
    vi.useFakeTimers(); const parent = new AbortController();
    let finish!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise(done => { finish = done; }));
    const action = rejected(list(parent.signal, 20));
    if (mode === 'cancel') parent.abort(); else await vi.advanceTimersByTimeAsync(10_000);
    finish(tracedResponse({ detail: PRIVATE }, 503, TRACE_B));
    expect(await action).toMatchObject({ status: null, traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(`${WORKFLOWS_PATH}?limit=20&offset=20`);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });
});

for (const target of ['list', 'detail'] as const) describe(`${target}只读HTTP失败排错编号`, () => {
  const call = (parent = signal()) => target === 'list' ? list(parent)
    : readAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID,
      { id: WORKFLOW_ID, question: QUESTION, maxContextCharacters: 12000 }, parent);

  it.each([401, 403, 404, 409, 422, 503, 201])('HTTP %s只携带本次响应头编号，不读取错误正文或重试', async status => {
    const response = tracedResponse({ detail: PRIVATE, trace_id: TRACE_B }, status, TRACE_A.toUpperCase());
    const body = vi.spyOn(response, 'json'); fetchMock.mockResolvedValueOnce(response);
    expect(await rejected(call())).toMatchObject({ status, traceId: TRACE_A });
    expect(body).not.toHaveBeenCalled(); expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1]?.method).toBe('GET');
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).has('X-Trace-Id')).toBe(false);
  });

  it.each([undefined, 'not-a-uuid', TRACE_A.replace('-4ae0-', '-1ae0-'), `${TRACE_A}, ${TRACE_B}`])(
    '缺失或非法头%s不从错误JSON补编号', async trace => {
      fetchMock.mockResolvedValueOnce(tracedResponse({ detail: PRIVATE, trace_id: TRACE_B }, 503, trace));
      expect(await rejected(call())).toMatchObject({ status: 503, traceId: null });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

  it.each(['invalid-json', 'invalid-contract'])('200的%s不冒充有失败响应编号', async problem => {
    fetchMock.mockResolvedValueOnce(problem === 'invalid-json'
      ? new Response(PRIVATE, { headers: { 'X-Trace-Id': TRACE_A } })
      : tracedResponse({ private: PRIVATE }, 200, TRACE_A));
    expect(await rejected(call())).toMatchObject({ status: null, traceId: null });
  });

  it('网络错误不从异常文本提取编号', async () => {
    fetchMock.mockRejectedValueOnce(new Error(`${PRIVATE} ${TRACE_A}`));
    expect(await rejected(call())).toMatchObject({ traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(['cancel', 'timeout'])('%s后迟到的失败响应不恢复编号，且不重试', async mode => {
    vi.useFakeTimers(); const parent = new AbortController();
    let finish!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise(done => { finish = done; }));
    const action = rejected(call(parent.signal));
    if (mode === 'cancel') parent.abort(); else await vi.advanceTimersByTimeAsync(10_000);
    finish(tracedResponse({ detail: PRIVATE }, 503, TRACE_A));
    expect(await action).toMatchObject({ traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });

  it('预先取消无HTTP请求，也没有编号', async () => {
    const parent = new AbortController(); parent.abort();
    expect(await rejected(call(parent.signal))).toMatchObject({ traceId: null });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('成功结果不投影响应头编号为工作流字段', async () => {
    fetchMock.mockResolvedValueOnce(tracedResponse(target === 'list' ? [summary()] : createdWorkflow(), 200, TRACE_A));
    const result = await call(); expect(JSON.stringify(result)).not.toContain(TRACE_A);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
