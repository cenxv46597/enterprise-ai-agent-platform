import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './api';
import {
  WorkflowUnconfirmedError, createAnswerWorkflow, executeAnswerWorkflow, readAnswerWorkflow,
  type WorkflowMeta,
} from './workflow-api';
import {
  ABSTENTION, ANSWER, CHUNK_ID, DOCUMENT_ID, DOCUMENT_PATH, KNOWLEDGE_BASE_ID, LONG_QUOTE,
  OTHER_DOCUMENT_PATH, OTHER_KNOWLEDGE_BASE_ID, OTHER_QUOTE, PRIVATE, QUESTION, WORKSPACE_ID,
  documentResponse, jsonResponse,
} from './conversation-test-fixtures';
import {
  CREATION_REQUEST_ID, WORKFLOW_EXECUTION_REQUEST_ID, WORKFLOW_EXECUTE_PATH, WORKFLOW_ID,
  WORKFLOW_PATH, WORKFLOWS_PATH, abstainedWorkflow, completedWorkflow, createdWorkflow,
  failedWorkflow, runningWorkflow, workflowCheckpoint, workflowExecution,
} from './workflow-test-fixtures';

const fetchMock = vi.fn<typeof fetch>();
const signal = () => new AbortController().signal;
function initialMeta(): WorkflowMeta {
  return { id: WORKFLOW_ID, question: QUESTION, maxContextCharacters: 12000, status: 'pending',
    nextStep: 'retrieve', version: 0, attemptCount: 0, checkpointCount: null, errorCode: null, retryable: false };
}
function create(question = QUESTION, parent = signal()) {
  return createAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID,
    { requestId: CREATION_REQUEST_ID, question }, parent);
}
function execute(parent = signal()) {
  return executeAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID, initialMeta(),
    { requestId: WORKFLOW_EXECUTION_REQUEST_ID, expectedVersion: 0 }, parent);
}
function read(parent = signal()) {
  return readAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID, initialMeta(), parent);
}
async function rejected(action: Promise<unknown>): Promise<ApiError> {
  const error: unknown = await action.then(() => { throw new Error('Expected rejection'); }, failure => failure);
  expect(error).toBeInstanceOf(ApiError);
  expect(String(error)).not.toContain(PRIVATE);
  return error as ApiError;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(complete => { resolve = complete; });
  return { promise, resolve };
}
function mockSources() {
  fetchMock.mockResolvedValueOnce(jsonResponse(documentResponse()))
    .mockResolvedValueOnce(jsonResponse(documentResponse('dddddddd-dddd-dddd-dddd-dddddddddddd')));
}

beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('固定问答工作流的纯模拟HTTP契约', () => {
  it('创建只发送一次固定vector POST，不自动执行或读取来源', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(createdWorkflow(), 201));
    expect(await create(` \n${QUESTION}\t`)).toEqual(initialMeta());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(WORKFLOWS_PATH);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST', mode: 'same-origin',
      credentials: 'same-origin', redirect: 'error', cache: 'no-store',
      body: JSON.stringify({ request_id: CREATION_REQUEST_ID, question: QUESTION, strategy: 'vector' }) });
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get('X-EAAP-Web')).toBe('1');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.has('Authorization')).toBe(false);
    expect(headers.has('Cookie')).toBe(false);
  });

  it('创建接纳2000个Unicode码点，超限/空问题/非UUID在本地拒绝', async () => {
    const question = '🙂'.repeat(2000);
    fetchMock.mockResolvedValueOnce(jsonResponse(createdWorkflow(question), 201));
    expect((await create(question)).question).toBe(question);
    for (const question of ['', ' \n\t', '🙂'.repeat(2001)]) {
      expect(await rejected(create(question))).not.toBeInstanceOf(WorkflowUnconfirmedError);
    }
    for (const [workspace, knowledgeBase, requestId] of [
      [`${WORKSPACE_ID}\n`, KNOWLEDGE_BASE_ID, CREATION_REQUEST_ID],
      [WORKSPACE_ID, '../outside', CREATION_REQUEST_ID],
      [WORKSPACE_ID, KNOWLEDGE_BASE_ID, `${CREATION_REQUEST_ID}\n`],
    ]) {
      expect(await rejected(createAnswerWorkflow(workspace, knowledgeBase, { requestId, question: QUESTION }, signal())))
        .not.toBeInstanceOf(WorkflowUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('创建成功必须是201的初始状态，并绑定问题、空间、知识库、策略和冻结预算', async () => {
    const base = createdWorkflow();
    const invalid: unknown[] = [null, [], { ...base, workflow_id: `${WORKFLOW_ID}\n` },
      { ...base, workspace_id: CREATION_REQUEST_ID }, { ...base, knowledge_base_id: OTHER_KNOWLEDGE_BASE_ID },
      { ...base, question: `${QUESTION}别的问题` }, { ...base, strategy: 'hybrid' },
      { ...base, max_context_characters: 0 }, { ...base, max_context_characters: 12001 },
      { ...base, max_context_characters: true }, { ...base, max_context_characters: 1.5 },
      { ...base, version: 1 }, { ...base, attempt_count: 1 }, { ...base, checkpoint: workflowCheckpoint() },
      { ...base, error_code: 'service_error' }, { ...base, retryable: true },
      { ...base, active_request_id: WORKFLOW_EXECUTION_REQUEST_ID }, { ...base, lease_expires_at: '2026-10-17T12:30:00Z' },
      { ...base, status: 'completed', next_step: 'done', result: completedWorkflow().result },
      { ...base, result: completedWorkflow().result }];
    for (const payload of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload, 201));
      expect(await rejected(create())).toMatchObject({ name: 'WorkflowUnconfirmedError', stage: 'create' });
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(base));
    expect(await rejected(create())).toMatchObject({ stage: 'create', status: 200 });
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length + 1);
  });

  it('创建冻结预算可以小于12000，执行和读取必须保持创建时预算', async () => {
    const raw = { ...createdWorkflow(), max_context_characters: 1000 };
    fetchMock.mockResolvedValueOnce(jsonResponse(raw, 201));
    const created = await create();
    expect(created.maxContextCharacters).toBe(1000);
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...createdWorkflow(), max_context_characters: 1001 }));
    expect(await rejected(readAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID, created, signal())))
      .toMatchObject({ stage: 'read' });
  });

  it('首次执行只POST版本0/不恢复，完整答案核对全部来源后返回引用，不返回检查点正文', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution())); mockSources();
    const result = await execute();
    expect(result).toMatchObject({ requestId: WORKFLOW_EXECUTION_REQUEST_ID, attemptNumber: 1, status: 'completed',
      workflow: { id: WORKFLOW_ID, question: QUESTION, maxContextCharacters: 12000, status: 'completed',
        nextStep: 'done', version: 3, attemptCount: 1, checkpointCount: 2, errorCode: null, retryable: false },
      answer: { answer: ANSWER, abstained: false, citations: [
        { documentId: DOCUMENT_ID, documentName: '导出说明.md', chunkIndex: 1, quote: LONG_QUOTE },
        { documentName: '保留说明.md', chunkIndex: 2, quote: OTHER_QUOTE },
      ] } });
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([WORKFLOW_EXECUTE_PATH, DOCUMENT_PATH, OTHER_DOCUMENT_PATH]);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST', body: JSON.stringify({
      request_id: WORKFLOW_EXECUTION_REQUEST_ID, expected_version: 0, recover_interrupted: false }) });
    expect(result.workflow).not.toHaveProperty('checkpoint');
    expect(JSON.stringify(result)).not.toContain(PRIVATE);
  });

  it('同一文档多个文本块只核对文档一次，仍保留全部逐字引用', async () => {
    const workflow = completedWorkflow();
    const first = workflow.checkpoint.hits[0];
    workflow.checkpoint.hits[1] = { ...workflow.checkpoint.hits[1], document_id: first.document_id,
      document_name: first.document_name };
    workflow.result.citations[1] = { ...workflow.result.citations[1], document_id: first.document_id,
      document_name: first.document_name };
    fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution(workflow))).mockResolvedValueOnce(jsonResponse(documentResponse()));
    expect((await execute()).answer?.citations).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('正常弃答保留检查点元数据，不把空候选当错误，不发来源GET或重试', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution(abstainedWorkflow())));
    expect(await execute()).toMatchObject({ status: 'completed', workflow: { checkpointCount: 0, version: 3 },
      answer: { answer: ABSTENTION, abstained: true, citations: [] } });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(['retrieve', 'generate'] as const)('HTTP200的%s失败是已确认失败回执，并不自动恢复', async stage => {
    fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution(failedWorkflow(stage))));
    if (stage === 'generate') mockSources();
    expect(await execute()).toMatchObject({ status: 'failed', attemptNumber: 1, answer: null,
      workflow: { status: 'failed', version: stage === 'retrieve' ? 2 : 3,
        nextStep: stage, retryable: true, checkpointCount: stage === 'retrieve' ? null : 2 } });
    expect(fetchMock).toHaveBeenCalledTimes(stage === 'retrieve' ? 1 : 3);
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
  });

  it.each(['evidence_changed', 'checkpoint_invalid'])('检查点已保存后%s会清除检查点，接纳version3退回retrieve的失败回执', async errorCode => {
    const workflow = { ...failedWorkflow(), version: 3, error_code: errorCode, retryable: false };
    fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution(workflow)));
    expect(await execute()).toMatchObject({ status: 'failed', answer: null, workflow: {
      status: 'failed', version: 3, nextStep: 'retrieve', checkpointCount: null, errorCode, retryable: false,
    } });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('首次回执绑定请求UUID、attempt1、正确版本和最终状态，拒绝中断或重试回执', async () => {
    const base = workflowExecution();
    const invalid = [
      { ...base, request_id: CREATION_REQUEST_ID }, { ...base, request_id: `${WORKFLOW_EXECUTION_REQUEST_ID}\n` },
      { ...base, attempt_number: 2 }, { ...base, attempt_number: true }, { ...base, status: 'interrupted' },
      { ...base, status: 'failed' }, { ...base, workflow: { ...base.workflow, workflow_id: CREATION_REQUEST_ID } },
      { ...base, workflow: { ...base.workflow, workspace_id: CREATION_REQUEST_ID } },
      { ...base, workflow: { ...base.workflow, knowledge_base_id: OTHER_KNOWLEDGE_BASE_ID } },
      { ...base, workflow: { ...base.workflow, question: `${QUESTION}修改` } },
      { ...base, workflow: { ...base.workflow, max_context_characters: 11999 } },
      { ...base, workflow: { ...base.workflow, strategy: 'hybrid' } },
      { ...base, workflow: { ...base.workflow, version: 2 } },
      { ...base, workflow: { ...base.workflow, attempt_count: 2 } },
      workflowExecution({ ...failedWorkflow(), version: 3 }),
      workflowExecution({ ...failedWorkflow('generate'), version: 2 }),
    ];
    for (const payload of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      expect(await rejected(execute())).toMatchObject({ stage: 'execute' });
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('执行前拒绝非初始元数据、错版本、非法标识，不发送请求', async () => {
    for (const created of [
      { ...initialMeta(), version: 1 }, { ...initialMeta(), status: 'running' },
      { ...initialMeta(), id: `${WORKFLOW_ID}\n` }, { ...initialMeta(), attemptCount: 1 },
    ] as WorkflowMeta[]) {
      expect(await rejected(executeAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID, created,
        { requestId: WORKFLOW_EXECUTION_REQUEST_ID, expectedVersion: 0 }, signal()))).not.toBeInstanceOf(WorkflowUnconfirmedError);
    }
    expect(await rejected(executeAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID, initialMeta(),
      { requestId: WORKFLOW_EXECUTION_REQUEST_ID, expectedVersion: 1 as 0 }, signal()))).not.toBeInstanceOf(WorkflowUnconfirmedError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('拒绝检查点错schema/范围/策略、超过5块、重复块或同文档矛盾名称', async () => {
    const base = workflowCheckpoint();
    const invalid = [
      { ...base, schema_version: true }, { ...base, schema_version: 2 },
      { ...base, workspace_id: CREATION_REQUEST_ID }, { ...base, knowledge_base_id: OTHER_KNOWLEDGE_BASE_ID },
      { ...base, strategy: 'hybrid' }, { ...base, hits: null },
      { ...base, hits: [...base.hits, ...base.hits, ...base.hits] },
      { ...base, hits: [base.hits[0], { ...base.hits[1], chunk_id: CHUNK_ID.toUpperCase() }] },
      { ...base, hits: [base.hits[0], { ...base.hits[1], document_id: DOCUMENT_ID, document_name: '矛盾名.md' }] },
    ];
    for (const checkpoint of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...workflowExecution(), workflow: { ...completedWorkflow(), checkpoint } }));
      expect(await rejected(execute())).toMatchObject({ stage: 'execute' });
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('检查点正文按Unicode码点核对范围，拒绝非法distance/rank及正文字数越界', async () => {
    const hit = workflowCheckpoint().hits[0];
    const invalid: unknown[] = [
      { ...hit, chunk_id: `${CHUNK_ID}\n` }, { ...hit, document_name: '' },
      { ...hit, chunk_index: true }, { ...hit, chunk_index: 0 }, { ...hit, start_char: -1 },
      { ...hit, start_char: true }, { ...hit, end_char: hit.end_char + 1 },
      { ...hit, end_char: hit.start_char + LONG_QUOTE.length }, { ...hit, content: '' },
      { ...hit, content: '🙂'.repeat(12001), end_char: hit.start_char + 12001 },
      { ...hit, distance: -0.1 }, { ...hit, distance: 2.01 }, { ...hit, distance: true },
      { ...hit, distance: '0.1' }, { ...hit, fusion_score: -1 }, { ...hit, vector_rank: 0 },
      { ...hit, lexical_rank: true }, { ...hit, lexical_score: -1 },
      { ...hit, retrieval_rank: 1.5 }, { ...hit, rerank_rank: 0 },
    ];
    for (const value of invalid) {
      const checkpoint = { ...workflowCheckpoint(), hits: [value] };
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...workflowExecution(),
        workflow: { ...completedWorkflow(), checkpoint } }));
      expect(await rejected(execute())).toMatchObject({ stage: 'execute' });
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('检查点不能超过冻结正文预算或100000字节JSON界限', async () => {
    const large = { ...workflowCheckpoint(), internal_padding: PRIVATE.repeat(4000) };
    const smallBudget = { ...completedWorkflow(), max_context_characters: 1 };
    for (const workflow of [{ ...completedWorkflow(), checkpoint: large }, smallBudget]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution(workflow)));
      expect(await rejected(execute())).toMatchObject({ stage: 'execute' });
    }
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('答案引文必须逐字绑定检查点的块、文档、名称、编号与范围，不能只比较UUID', async () => {
    const base = completedWorkflow();
    const citation = base.result.citations[0];
    const invalid = [
      { ...citation, chunk_id: CREATION_REQUEST_ID }, { ...citation, document_id: CREATION_REQUEST_ID },
      { ...citation, document_name: '别的文档.md' }, { ...citation, chunk_index: 2 },
      { ...citation, start_char: citation.start_char - 1 }, { ...citation, end_char: citation.end_char + 1 },
      { ...citation, quote: `${LONG_QUOTE}篡改` },
      { ...citation, quote: LONG_QUOTE.replace('30', '31') },
    ];
    for (const value of invalid) {
      const workflow = { ...base, result: { ...base.result, citations: [value] } };
      fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution(workflow)));
      expect(await rejected(execute())).toMatchObject({ stage: 'execute' });
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('接纳同一块中的多段精确子引文，不要求引用覆盖完整块', async () => {
    const workflow = completedWorkflow();
    const source = workflow.result.citations[0];
    const quote = '每次上门服务费30元。';
    workflow.result.citations = [{ ...source, quote, end_char: source.start_char + Array.from(quote).length },
      { ...source, quote: '完整引文的结尾', start_char: source.end_char - Array.from('完整引文的结尾').length }];
    fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution(workflow))); mockSources();
    expect((await execute()).answer?.citations).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('结果与弃答必须一致，答案长度/引用上限/非空reranking均拒绝', async () => {
    const base = completedWorkflow();
    const invalid: unknown[] = [
      null, { ...base.result, answer: '' }, { ...base.result, answer: '🙂'.repeat(4001) },
      { ...base.result, abstained: true }, { ...base.result, citations: [] },
      { ...base.result, citations: Array.from({ length: 21 }, () => base.result.citations[0]) },
      { ...base.result, reranking: {} }, { ...base.result, reranking: undefined },
    ];
    for (const result of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...workflowExecution(), workflow: { ...base, result } }));
      expect(await rejected(execute())).toMatchObject({ stage: 'execute' });
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('全部检查点来源都核对，包括未被答案引用的来源，第二来源失败不能返回部分答案', async () => {
    const workflow = completedWorkflow(); workflow.result.citations = [workflow.result.citations[0]];
    fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution(workflow)))
      .mockResolvedValueOnce(jsonResponse(documentResponse()))
      .mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, 404));
    expect(await rejected(execute())).toMatchObject({ stage: 'execute' });
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([WORKFLOW_EXECUTE_PATH, DOCUMENT_PATH, OTHER_DOCUMENT_PATH]);
  });

  it('来源归属/文档ID/名称/ready任一不符均不能确认，401仍安全传给会话退出逻辑', async () => {
    for (const source of [
      { ...documentResponse(), id: CREATION_REQUEST_ID },
      { ...documentResponse(), knowledge_base_id: OTHER_KNOWLEDGE_BASE_ID },
      { ...documentResponse(), original_filename: '改名.md' }, { ...documentResponse(), status: 'processing' },
    ]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution())).mockResolvedValueOnce(jsonResponse(source));
      expect(await rejected(execute())).toBeInstanceOf(WorkflowUnconfirmedError);
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution())).mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, 401));
    const error = await rejected(execute()); expect(error.status).toBe(401);
    expect(error).not.toBeInstanceOf(WorkflowUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(10);
  });

  it('GET只返回元数据，不读取来源、展示答案或自动执行；running不等于失败', async () => {
    const states = [createdWorkflow(), runningWorkflow(), runningWorkflow('generate'),
      failedWorkflow(), failedWorkflow('generate'), completedWorkflow(), abstainedWorkflow()];
    for (const workflow of states) {
      fetchMock.mockResolvedValueOnce(jsonResponse(workflow));
      const current = await read();
      expect(current).toMatchObject({ id: WORKFLOW_ID, question: QUESTION, status: workflow.status,
        version: workflow.version, checkpointCount: workflow.checkpoint?.hits.length ?? null });
      expect(current).not.toHaveProperty('answer');
      expect(current).not.toHaveProperty('result');
      expect(current).not.toHaveProperty('checkpoint');
    }
    expect(fetchMock).toHaveBeenCalledTimes(states.length);
    for (const [path, options] of fetchMock.mock.calls) {
      expect(path).toBe(WORKFLOW_PATH); expect(options?.method).toBe('GET'); expect(options?.body).toBeUndefined();
    }
  });

  it('GET校验运行租约、错误码、可恢复标记、步骤和版本边界，不用缺失数据编造成功', async () => {
    const base = createdWorkflow();
    const invalid = [
      { ...base, workflow_id: CREATION_REQUEST_ID }, { ...base, question: PRIVATE },
      { ...base, version: -1 }, { ...base, version: 33 }, { ...base, version: true },
      { ...base, attempt_count: 6 }, { ...base, next_step: 'done' }, { ...base, retryable: true },
      { ...runningWorkflow(), active_request_id: null }, { ...runningWorkflow(), lease_expires_at: null },
      { ...runningWorkflow(), lease_expires_at: 'not-a-date' }, { ...runningWorkflow(), lease_expires_at: '2026-10-17T12:30:00' },
      { ...failedWorkflow(), error_code: PRIVATE }, { ...failedWorkflow(), error_code: null },
      { ...failedWorkflow(), retryable: false }, { ...failedWorkflow(), error_code: 'context_limit', retryable: true },
      { ...completedWorkflow(), checkpoint: null }, { ...completedWorkflow(), result: null },
    ];
    for (const workflow of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse(workflow));
      expect(await rejected(read())).toMatchObject({ stage: 'read' });
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it.each(['create', 'execute', 'read'] as const)('%s的已知HTTP拒绝使用固定安全信息，不读取原始detail也不重试', async stage => {
    for (const status of [401, 403, 404, 409, 422]) {
      const response = jsonResponse({ detail: PRIVATE }, status); const json = vi.spyOn(response, 'json');
      fetchMock.mockResolvedValueOnce(response);
      const error = await rejected(stage === 'create' ? create() : stage === 'execute' ? execute() : read());
      expect(error.status).toBe(status); expect(error).not.toBeInstanceOf(WorkflowUnconfirmedError);
      expect(json).not.toHaveBeenCalled();
    }
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it.each(['create', 'execute', 'read'] as const)('%s的网络/坏JSON/未知HTTP响应保持未确认，不自动读取状态或重试', async stage => {
    const action = () => stage === 'create' ? create() : stage === 'execute' ? execute() : read();
    for (const status of [202, 204, 429, 500, 502, 503, 504]) {
      fetchMock.mockResolvedValueOnce(status === 204 ? new Response(null, { status }) : jsonResponse({ detail: PRIVATE }, status));
      expect(await rejected(action())).toMatchObject({ name: 'WorkflowUnconfirmedError', stage, status });
    }
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE, { status: stage === 'create' ? 201 : 200 }));
    expect(await rejected(action())).toMatchObject({ stage });
    fetchMock.mockRejectedValueOnce(new TypeError(PRIVATE)); expect(await rejected(action())).toMatchObject({ stage });
    expect(fetchMock).toHaveBeenCalledTimes(9);
  });

  it('已取消的创建、执行和读取均不发请求', async () => {
    const parent = new AbortController(); parent.abort();
    expect(await rejected(create(QUESTION, parent.signal))).toMatchObject({ stage: 'create' });
    expect(await rejected(execute(parent.signal))).toMatchObject({ stage: 'execute' });
    expect(await rejected(read(parent.signal))).toMatchObject({ stage: 'read' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['create', 'read'] as const)('%s只等待十秒，迟到响应不会确认结果或自动执行', async stage => {
    vi.useFakeTimers(); const pending = deferred<Response>(); fetchMock.mockReturnValueOnce(pending.promise);
    const action = rejected(stage === 'create' ? create() : read());
    await vi.advanceTimersByTimeAsync(9_999); expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1); expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    pending.resolve(jsonResponse(createdWorkflow(), stage === 'create' ? 201 : 200));
    expect(await action).toMatchObject({ stage }); expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('执行等待630秒，迟到完成回执不触发来源GET，也不再次执行', async () => {
    vi.useFakeTimers(); const pending = deferred<Response>(); fetchMock.mockReturnValueOnce(pending.promise);
    const action = rejected(execute());
    await vi.advanceTimersByTimeAsync(10_000); expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(619_999); expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1); expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    pending.resolve(jsonResponse(workflowExecution()));
    expect(await action).toMatchObject({ stage: 'execute' }); expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('响应JSON解析期间取消后，迟到JSON不能触发来源读取或确认答案', async () => {
    const pending = deferred<unknown>(); const response = jsonResponse(workflowExecution());
    const json = vi.spyOn(response, 'json').mockReturnValueOnce(pending.promise);
    fetchMock.mockResolvedValueOnce(response); const parent = new AbortController(); const action = rejected(execute(parent.signal));
    await vi.waitFor(() => expect(json).toHaveBeenCalledTimes(1)); parent.abort(); pending.resolve(workflowExecution());
    expect(await action).toMatchObject({ stage: 'execute' }); expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('来源读取期间取消后，迟到来源不能继续下一来源或返回部分答案', async () => {
    const pending = deferred<Response>(); fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution()))
      .mockReturnValueOnce(pending.promise);
    const parent = new AbortController(); const action = rejected(execute(parent.signal));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2)); parent.abort(); pending.resolve(jsonResponse(documentResponse()));
    expect(await action).toMatchObject({ stage: 'execute' }); expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('长执行结束后来源仍有独立十秒等待，超时不返回答案，并清理监听', async () => {
    vi.useFakeTimers(); const pending = deferred<Response>();
    fetchMock.mockReturnValueOnce(pending.promise).mockImplementationOnce((_path, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new TypeError(PRIVATE)), { once: true });
    }));
    const parent = new AbortController(); const remove = vi.spyOn(parent.signal, 'removeEventListener');
    const action = rejected(execute(parent.signal)); await vi.advanceTimersByTimeAsync(629_999);
    pending.resolve(jsonResponse(workflowExecution())); await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2); await vi.advanceTimersByTimeAsync(9_999);
    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(false); await vi.advanceTimersByTimeAsync(1);
    expect(await action).toMatchObject({ stage: 'execute' }); expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2); expect(vi.getTimerCount()).toBe(0);
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
  });
});
