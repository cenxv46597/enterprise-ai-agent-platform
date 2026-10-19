import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { canReadSavedWorkflowAnswer, canResumeWorkflow, readAnswerWorkflow, readSavedWorkflowAnswer,
  WorkflowUnconfirmedError, type WorkflowMeta } from './workflow-api';
import { ABSTENTION, ANSWER, BASE_PATH, CHUNK_ID, DOCUMENT_ID, DOCUMENT_PATH, KNOWLEDGE_BASE_ID,
  LONG_QUOTE, OTHER_DOCUMENT_ID, OTHER_DOCUMENT_PATH, OTHER_KNOWLEDGE_BASE_ID, PRIVATE, QUESTION,
  WORKSPACE_ID, documentResponse, jsonResponse } from './conversation-test-fixtures';
import { WORKFLOW_ID, WORKFLOW_PATH, abstainedWorkflow, completedWorkflow, createdWorkflow,
  failedWorkflow, runningWorkflow } from './workflow-test-fixtures';

const fetchMock = vi.fn<typeof fetch>();
const signal = () => new AbortController().signal;
const PREFIX_CHUNK_ID = '13131313-1313-1313-1313-131313131313';
function initialMeta(): WorkflowMeta {
  return { id: WORKFLOW_ID, question: QUESTION, maxContextCharacters: 12000,
    status: 'pending', nextStep: 'retrieve', version: 0, attemptCount: 0, checkpointCount: null,
    errorCode: null, retryable: false };
}
// These checkpoint hits are full chunks at real list positions, including an unselected prefix.
function savedState() {
  const state = completedWorkflow();
  state.checkpoint.hits[0].chunk_index = 2;
  state.checkpoint.hits[1].chunk_index = 1;
  state.result.citations[0].chunk_index = 2;
  state.result.citations[1].chunk_index = 1;
  return state;
}
function documentChunks(documentId: string, state = savedState()) {
  const hits = state.checkpoint.hits.filter(hit => hit.document_id === documentId);
  const selected = hits.map(hit => ({ id: hit.chunk_id, document_id: hit.document_id,
    chunk_index: hit.chunk_index, content: hit.content, start_char: hit.start_char, end_char: hit.end_char }));
  return documentId === DOCUMENT_ID ? [{ id: PREFIX_CHUNK_ID, document_id: DOCUMENT_ID,
    chunk_index: 1, content: '前'.repeat(110), start_char: 0, end_char: 110 }, ...selected] : selected;
}
async function setup(state: ReturnType<typeof createdWorkflow> = savedState()) {
  fetchMock.mockReset();
  const created = initialMeta();
  fetchMock.mockResolvedValueOnce(jsonResponse(state));
  const current = await readAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID, created, signal());
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockReset();
  return { created, current };
}
function read(created: WorkflowMeta, current: WorkflowMeta, parent = signal()) {
  return readSavedWorkflowAnswer(WORKSPACE_ID, KNOWLEDGE_BASE_ID, created, current, parent);
}
function sources(state = savedState()) {
  fetchMock.mockResolvedValueOnce(jsonResponse(documentResponse()))
    .mockResolvedValueOnce(jsonResponse(documentChunks(DOCUMENT_ID, state)))
    .mockResolvedValueOnce(jsonResponse(documentResponse(OTHER_DOCUMENT_ID)))
    .mockResolvedValueOnce(jsonResponse(documentChunks(OTHER_DOCUMENT_ID, state)));
}
function success(state = savedState()) { fetchMock.mockResolvedValueOnce(jsonResponse(state)); sources(state); }
beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

const TRACE_A = 'a0e0b901-2b0d-4ae0-812a-bab2cf9e7b01';
const TRACE_B = 'b1f1c012-3c1e-4bf1-923b-cbc3dfa08c12';
function traced(payload: unknown, status = 200, trace?: string) {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (trace !== undefined) headers.set('X-Trace-Id', trace);
  return new Response(JSON.stringify(payload), { status, headers });
}
function numberedChain() {
  return [traced(savedState(), 200, TRACE_A), traced(documentResponse(), 200, TRACE_A),
    traced(documentChunks(DOCUMENT_ID), 200, TRACE_A), traced(documentResponse(OTHER_DOCUMENT_ID), 200, TRACE_A),
    traced(documentChunks(OTHER_DOCUMENT_ID), 200, TRACE_A)];
}
async function failedRead(created: WorkflowMeta, current: WorkflowMeta, parent = signal()) {
  const error = await read(created, current, parent).catch(error => error);
  expect(error).toBeInstanceOf(ApiError); expect(String(error)).not.toContain(PRIVATE);
  for (const [, options] of fetchMock.mock.calls) {
    expect(options?.method).toBe('GET'); expect(options?.body).toBeUndefined();
    expect(new Headers(options?.headers).has('X-Trace-Id')).toBe(false);
  }
  return error as ApiError;
}

describe('已保存答案只读核验失败的响应编号', () => {
  it.each([401, 404, 503, 201])('工作流GET非预期HTTP%s携带自身头编号，不读错误JSON或开始来源核验', async status => {
    const { created, current } = await setup();
    const response = traced({ detail: PRIVATE, trace_id: TRACE_A }, status, TRACE_B.toUpperCase());
    const body = vi.spyOn(response, 'json'); fetchMock.mockResolvedValueOnce(response);
    expect(await failedRead(created, current)).toMatchObject({ status, traceId: TRACE_B });
    expect(body).not.toHaveBeenCalled(); expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  for (const index of [1, 2, 3, 4]) it.each([401, 404, 503])(
    `来源第${index}个GET的HTTP%s只用失败响应编号，不沿用之前成功请求`, async status => {
      const { created, current } = await setup();
      const responses = numberedChain();
      for (const response of responses.slice(0, index)) fetchMock.mockResolvedValueOnce(response);
      const failure = traced({ detail: PRIVATE, trace_id: TRACE_A }, status, TRACE_B);
      const body = vi.spyOn(failure, 'json'); fetchMock.mockResolvedValueOnce(failure);
      expect(await failedRead(created, current)).toMatchObject({ status, traceId: TRACE_B });
      expect(body).not.toHaveBeenCalled(); expect(fetchMock).toHaveBeenCalledTimes(index + 1);
    });

  for (const index of [0, 4]) it.each([undefined, 'not-a-uuid', TRACE_B.replace('-4bf1-', '-1bf1-'), `${TRACE_A}, ${TRACE_B}`])(
    `第${index}个GET头%s无效时，不从成功头或错误JSON补编号`, async trace => {
      const { created, current } = await setup();
      for (const response of numberedChain().slice(0, index)) fetchMock.mockResolvedValueOnce(response);
      fetchMock.mockResolvedValueOnce(traced({ detail: PRIVATE, trace_id: TRACE_B }, 503, trace));
      expect(await failedRead(created, current)).toMatchObject({ traceId: null });
      expect(fetchMock).toHaveBeenCalledTimes(index + 1);
    });

  it.each(['workflow', 'document', 'chunks'] as const)('200后%s快照/来源核验失败不使用成功头编号', async target => {
    const { created, current } = await setup();
    if (target === 'workflow') {
      fetchMock.mockResolvedValueOnce(traced({ ...savedState(), version: 4 }, 200, TRACE_B));
    } else {
      fetchMock.mockResolvedValueOnce(traced(savedState(), 200, TRACE_A));
      if (target === 'document') {
        fetchMock.mockResolvedValueOnce(traced({ ...documentResponse(), original_filename: 'changed' }, 200, TRACE_B));
      } else {
        fetchMock.mockResolvedValueOnce(traced(documentResponse(), 200, TRACE_A));
        const chunks = documentChunks(DOCUMENT_ID);
        chunks[1].content = '变'.repeat(Array.from(chunks[1].content).length);
        fetchMock.mockResolvedValueOnce(traced(chunks, 200, TRACE_B));
      }
    }
    expect(await failedRead(created, current)).toMatchObject({ status: null, traceId: null });
  });

  it.each([0, 1, 2])('第%s个GET的200坏JSON不展示成功头编号', async index => {
    const { created, current } = await setup();
    for (const response of numberedChain().slice(0, index)) fetchMock.mockResolvedValueOnce(response);
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE, { headers: { 'X-Trace-Id': TRACE_B } }));
    expect(await failedRead(created, current)).toMatchObject({ traceId: null });
  });

  it('来源201保留既有status=null语义，同时携带该响应编号', async () => {
    const { created, current } = await setup();
    fetchMock.mockResolvedValueOnce(traced(savedState(), 200, TRACE_A))
      .mockResolvedValueOnce(traced(documentResponse(), 201, TRACE_B));
    expect(await failedRead(created, current)).toMatchObject({ status: null, traceId: TRACE_B });
  });

  it.each([0, 4])('第%s个GET无响应时不从异常文本或之前成功请求取得编号', async index => {
    const { created, current } = await setup();
    for (const response of numberedChain().slice(0, index)) fetchMock.mockResolvedValueOnce(response);
    fetchMock.mockRejectedValueOnce(new Error(`${PRIVATE} ${TRACE_B}`));
    expect(await failedRead(created, current)).toMatchObject({ traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(index + 1);
  });

  it.each(['cancel', 'timeout'] as const)('末来源%s后迟到失败不会恢复编号或追加请求', async mode => {
    const { created, current } = await setup(); vi.useFakeTimers();
    for (const response of numberedChain().slice(0, 4)) fetchMock.mockResolvedValueOnce(response);
    let finish!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise(done => { finish = done; }));
    const parent = new AbortController(), action = failedRead(created, current, parent.signal);
    await vi.advanceTimersByTimeAsync(0); expect(fetchMock).toHaveBeenCalledTimes(5);
    if (mode === 'cancel') parent.abort(); else await vi.advanceTimersByTimeAsync(10_000);
    finish(traced({ detail: PRIVATE }, 503, TRACE_B));
    expect(await action).toMatchObject({ traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(5); expect(vi.getTimerCount()).toBe(0);
  });

  it('全部GET成功也不把任何响应编号投影为答案/任务字段', async () => {
    const { created, current } = await setup();
    for (const response of numberedChain()) fetchMock.mockResolvedValueOnce(response);
    const result = await read(created, current);
    expect(result.answer.answer).toBe(ANSWER); expect(JSON.stringify(result)).not.toContain(TRACE_A);
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });
});

describe('显式只读查看已保存工作流答案', () => {
  it('元数据GET先不展示正文，单独查看时核对全部原文块且只发送GET，不制造执行回执', async () => {
    const { created, current } = await setup();
    expect(canReadSavedWorkflowAnswer(current)).toBe(true);
    expect(current).not.toHaveProperty('answer');
    success();
    const result = await read(created, current);
    expect(result).toMatchObject({ workflow: { status: 'completed', version: 3, attemptCount: 1 },
      answer: { answer: ANSWER, abstained: false } });
    expect(result.answer.citations[0]).toMatchObject({ chunkId: CHUNK_ID, chunkIndex: 2, quote: LONG_QUOTE });
    expect(result).not.toHaveProperty('requestId');
    expect(result).not.toHaveProperty('attemptNumber');
    expect(canResumeWorkflow(null, result.workflow)).toBe(false);
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([WORKFLOW_PATH, DOCUMENT_PATH,
      `${DOCUMENT_PATH}/chunks`, OTHER_DOCUMENT_PATH, `${OTHER_DOCUMENT_PATH}/chunks`]);
    for (const [, options] of fetchMock.mock.calls) {
      expect(options?.method).toBe('GET'); expect(options?.body).toBeUndefined();
      expect(options?.credentials).toBe('same-origin');
    }
  });

  it('只认真实已完成元数据GET，创建/失败/运行状态及复制伪造对象均不发送查看', async () => {
    expect(canReadSavedWorkflowAnswer(null)).toBe(false);
    const completed = await setup();
    const copied = { ...completed.current };
    expect(canReadSavedWorkflowAnswer(copied)).toBe(false);
    await expect(read(completed.created, copied)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
    for (const state of [createdWorkflow(), runningWorkflow(), failedWorkflow('generate')]) {
      const { created, current } = await setup(state);
      expect(canReadSavedWorkflowAnswer(current)).toBe(false);
      Object.assign(current, completed.current);
      expect(canReadSavedWorkflowAnswer(current)).toBe(false);
      await expect(read(created, current)).rejects.toBeInstanceOf(ApiError);
      expect(fetchMock).not.toHaveBeenCalled();
    }
  });

  it.each(['id', 'question', 'budget', 'status', 'step', 'version', 'attempt', 'count', 'error', 'retryable'])(
    '公开元数据%s被修改后，私有原始快照不能被一起修改或放行', async field => {
      const { created, current } = await setup();
      switch (field) {
        case 'id': current.id = PREFIX_CHUNK_ID; break;
        case 'question': current.question += '修改'; break;
        case 'budget': current.maxContextCharacters -= 1; break;
        case 'status': current.status = 'failed'; break;
        case 'step': current.nextStep = 'generate'; break;
        case 'version': current.version += 1; break;
        case 'attempt': current.attemptCount += 1; break;
        case 'count': current.checkpointCount = 1; break;
        case 'error': current.errorCode = 'generation_error'; break;
        case 'retryable': current.retryable = true; break;
      }
      expect(canReadSavedWorkflowAnswer(current)).toBe(false);
      await expect(read(created, current)).rejects.toBeInstanceOf(ApiError);
      expect(fetchMock).not.toHaveBeenCalled();
    });

  it('跨工作区/知识库、创建任务标识/问题/冻结预算不匹配在本地拒绝', async () => {
    const { created, current } = await setup();
    for (const [workspace, knowledgeBase, creation] of [
      [PREFIX_CHUNK_ID, KNOWLEDGE_BASE_ID, created],
      [WORKSPACE_ID, OTHER_KNOWLEDGE_BASE_ID, created],
      [WORKSPACE_ID, KNOWLEDGE_BASE_ID, { ...created, id: PREFIX_CHUNK_ID }],
      [WORKSPACE_ID, KNOWLEDGE_BASE_ID, { ...created, question: '另一问题' }],
      [WORKSPACE_ID, KNOWLEDGE_BASE_ID, { ...created, maxContextCharacters: 1 }],
    ] as const) {
      await expect(readSavedWorkflowAnswer(workspace, knowledgeBase, creation, current, signal())).rejects.toBeInstanceOf(ApiError);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['scope', 'id', 'question', 'budget', 'strategy', 'version', 'attempt', 'status', 'order',
    'unquoted-text', 'range', 'chunk-index', 'distance', 'rank', 'score', 'answer', 'citation', 'invalid-answer'])(
    '第二次工作流GET的%s与已读完整快照不符，全部拒绝且不读取来源', async field => {
      const state = savedState();
      state.result.citations = [state.result.citations[0]];
      const { created, current } = await setup(state);
      const changed = structuredClone(state);
      switch (field) {
        case 'scope': changed.workspace_id = PREFIX_CHUNK_ID; break;
        case 'id': changed.workflow_id = PREFIX_CHUNK_ID; break;
        case 'question': changed.question += '修改'; break;
        case 'budget': changed.max_context_characters -= 1; break;
        case 'strategy': changed.strategy = 'hybrid'; break;
        case 'version': changed.version += 1; break;
        case 'attempt': changed.attempt_count += 1; break;
        case 'status': Object.assign(changed, failedWorkflow('generate')); break;
        case 'order': changed.checkpoint.hits.reverse(); break;
        case 'unquoted-text': changed.checkpoint.hits[1].content = '甲'.repeat(Array.from(changed.checkpoint.hits[1].content).length); break;
        case 'range': changed.checkpoint.hits[1].start_char += 1; changed.checkpoint.hits[1].end_char += 1; break;
        case 'chunk-index': changed.checkpoint.hits[1].chunk_index += 1; break;
        case 'distance': changed.checkpoint.hits[1].distance = 0.3; break;
        case 'rank': Object.assign(changed.checkpoint.hits[1], { vector_rank: 1 }); break;
        case 'score': Object.assign(changed.checkpoint.hits[1], { fusion_score: 0.5 }); break;
        case 'answer': changed.result.answer += '变化'; break;
        case 'citation': changed.result.citations[0].quote = '每次上门服务费30元。';
          changed.result.citations[0].end_char = changed.result.citations[0].start_char + Array.from(changed.result.citations[0].quote).length; break;
        case 'invalid-answer': changed.result.citations[0].quote = '与检查点不相符'; break;
      }
      fetchMock.mockResolvedValueOnce(jsonResponse(changed));
      await expect(read(created, current)).rejects.toMatchObject({ name: 'WorkflowUnconfirmedError', stage: 'answer-read' });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

  it('未引用的候选来源块也核对全文，第二来源变化不能返回第一来源的部分答案', async () => {
    const state = savedState(); state.result.citations = [state.result.citations[0]];
    const { created, current } = await setup(state);
    fetchMock.mockResolvedValueOnce(jsonResponse(state))
      .mockResolvedValueOnce(jsonResponse(documentResponse()))
      .mockResolvedValueOnce(jsonResponse(documentChunks(DOCUMENT_ID, state)))
      .mockResolvedValueOnce(jsonResponse(documentResponse(OTHER_DOCUMENT_ID)));
    const changed = documentChunks(OTHER_DOCUMENT_ID, state);
    changed[0].content = '甲'.repeat(Array.from(changed[0].content).length);
    fetchMock.mockResolvedValueOnce(jsonResponse(changed));
    await expect(read(created, current)).rejects.toMatchObject({ stage: 'answer-read' });
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it.each(['id', 'knowledge-base', 'name', 'status'])(
    '当前来源文档的%s不匹配时拒绝，不继续读取块或返回正文', async field => {
      const { created, current } = await setup();
      const document = documentResponse();
      switch (field) {
        case 'id': document.id = OTHER_DOCUMENT_ID; break;
        case 'knowledge-base': document.knowledge_base_id = OTHER_KNOWLEDGE_BASE_ID; break;
        case 'name': document.original_filename = '改名.md'; break;
        case 'status': document.status = 'processing'; break;
      }
      fetchMock.mockResolvedValueOnce(jsonResponse(savedState())).mockResolvedValueOnce(jsonResponse(document));
      await expect(read(created, current)).rejects.toMatchObject({ stage: 'answer-read' });
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

  it.each(['id', 'document', 'index', 'text', 'range', 'missing', 'empty', 'malformed'])(
    '当前完整分块列表的%s不匹配时拒绝，不能只比较文本块编号', async field => {
      const { created, current } = await setup();
      const chunks = documentChunks(DOCUMENT_ID);
      switch (field) {
        case 'id': chunks[1].id = WORKFLOW_ID; break;
        case 'document': chunks[1].document_id = OTHER_DOCUMENT_ID; break;
        case 'index': chunks[1].chunk_index = 3; break;
        case 'text': chunks[1].content = chunks[1].content.replace('30', '31'); break;
        case 'range': chunks[1].start_char += 1; chunks[1].end_char += 1; break;
        case 'missing': chunks.pop(); break;
        case 'empty': chunks.length = 0; break;
        case 'malformed': chunks[1].end_char += 1; break;
      }
      fetchMock.mockResolvedValueOnce(jsonResponse(savedState())).mockResolvedValueOnce(jsonResponse(documentResponse()))
        .mockResolvedValueOnce(jsonResponse(chunks));
      await expect(read(created, current)).rejects.toMatchObject({ stage: 'answer-read' });
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });

  it('同一文档多个候选块按UUID逐项核对，文档与完整块列表各读取一次', async () => {
    const state = savedState();
    const first = state.checkpoint.hits[0], second = state.checkpoint.hits[1];
    Object.assign(second, { document_id: first.document_id, document_name: first.document_name,
      chunk_index: 3, start_char: first.end_char - 1,
      end_char: first.end_char - 1 + Array.from(second.content).length });
    Object.assign(state.result.citations[1], { ...second, quote: second.content });
    const { created, current } = await setup(state);
    fetchMock.mockResolvedValueOnce(jsonResponse(state)).mockResolvedValueOnce(jsonResponse(documentResponse()))
      .mockResolvedValueOnce(jsonResponse(documentChunks(DOCUMENT_ID, state)));
    expect((await read(created, current)).answer.citations).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('正常空检查点弃答只读任务，不请求来源；非空检查点弃答仍核对全部来源', async () => {
    const empty = abstainedWorkflow();
    const first = await setup(empty);
    fetchMock.mockResolvedValueOnce(jsonResponse(empty));
    expect(await read(first.created, first.current)).toMatchObject({ answer: {
      answer: ABSTENTION, abstained: true, citations: [] }, workflow: { checkpointCount: 0 } });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const nonempty = savedState();
    nonempty.result = { answer: ABSTENTION, abstained: true, citations: [], reranking: null };
    const second = await setup(nonempty); success(nonempty);
    expect((await read(second.created, second.current)).answer.abstained).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it('返回结果被调用方修改，不会污染元数据GET的私有答案与块快照', async () => {
    const { created, current } = await setup(); success();
    const first = await read(created, current);
    first.answer.answer = PRIVATE; first.answer.citations[0].quote = PRIVATE;
    first.workflow.version = 9;
    expect(canReadSavedWorkflowAnswer(current)).toBe(true);
    fetchMock.mockReset(); success();
    expect((await read(created, current)).answer.answer).toBe(ANSWER);
  });

  it.each([401, 403, 404, 409, 422])('任务GET的HTTP%s保持固定安全错误和状态，不解析原始detail或重试', async status => {
    const { created, current } = await setup();
    const response = jsonResponse({ detail: PRIVATE }, status), json = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    const error = await read(created, current).catch(error => error);
    expect(error).toBeInstanceOf(ApiError); expect(error.status).toBe(status);
    expect(String(error)).not.toContain(PRIVATE); expect(json).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([202, 429, 500, 502, 503, 504])('任务GET的未知HTTP%s保持答案未确认且不重试', async status => {
    const { created, current } = await setup();
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, status));
    const error = await read(created, current).catch(error => error);
    expect(error).toMatchObject({ name: 'WorkflowUnconfirmedError', stage: 'answer-read', status });
    expect(String(error)).not.toContain(PRIVATE); expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('坏JSON或网络失败保持只读答案未确认，无后续来源或写请求', async () => {
    const { created, current } = await setup();
    for (const response of [new Response(PRIVATE), jsonResponse(null)]) {
      fetchMock.mockResolvedValueOnce(response);
      await expect(read(created, current)).rejects.toMatchObject({ stage: 'answer-read' });
    }
    fetchMock.mockRejectedValueOnce(new TypeError(PRIVATE));
    const error = await read(created, current).catch(error => error);
    expect(error).toBeInstanceOf(WorkflowUnconfirmedError);
    expect(String(error)).not.toContain(PRIVATE); expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it.each(['document', 'chunks'] as const)('来源%s的401/404保留登录失效或不存在语义，无原始detail', async source => {
    const { created, current } = await setup();
    for (const status of [401, 404]) {
      fetchMock.mockReset(); fetchMock.mockResolvedValueOnce(jsonResponse(savedState()));
      if (source === 'chunks') fetchMock.mockResolvedValueOnce(jsonResponse(documentResponse()));
      fetchMock.mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, status));
      const error = await read(created, current).catch(error => error);
      expect(error).toBeInstanceOf(ApiError); expect(error.status).toBe(status);
      expect(error).not.toBeInstanceOf(WorkflowUnconfirmedError);
      expect(String(error)).not.toContain(PRIVATE);
      expect(fetchMock).toHaveBeenCalledTimes(source === 'chunks' ? 3 : 2);
    }
  });

  it('已取消的查看不发请求', async () => {
    const { created, current } = await setup(); const parent = new AbortController(); parent.abort();
    await expect(read(created, current, parent.signal)).rejects.toMatchObject({ stage: 'answer-read' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['workflow', 'document', 'chunks'] as const)('%s GET各自最多等待十秒，迟到响应不能继续或展示', async stage => {
    const { created, current } = await setup(); vi.useFakeTimers();
    const responses = [jsonResponse(savedState()), jsonResponse(documentResponse()), jsonResponse(documentChunks(DOCUMENT_ID))];
    const pendingIndex = stage === 'workflow' ? 0 : stage === 'document' ? 1 : 2;
    for (let index = 0; index < pendingIndex; index += 1) fetchMock.mockResolvedValueOnce(responses[index]);
    let resolve!: (response: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>(done => { resolve = done; }));
    const action = read(created, current).catch(error => error);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(pendingIndex + 1);
    await vi.advanceTimersByTimeAsync(9_999);
    expect(fetchMock.mock.calls[pendingIndex][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock.mock.calls[pendingIndex][1]?.signal?.aborted).toBe(true);
    resolve(responses[pendingIndex]);
    expect(await action).toMatchObject({ stage: 'answer-read' });
    expect(fetchMock).toHaveBeenCalledTimes(pendingIndex + 1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('JSON解析或分块读取期间取消，迟到结果不继续核对或返回正文', async () => {
    const { created, current } = await setup();
    let resolveJson!: (payload: unknown) => void;
    const response = jsonResponse(savedState());
    const json = vi.spyOn(response, 'json').mockReturnValueOnce(new Promise(done => { resolveJson = done; }));
    fetchMock.mockResolvedValueOnce(response);
    const firstController = new AbortController(), first = read(created, current, firstController.signal).catch(error => error);
    await vi.waitFor(() => expect(json).toHaveBeenCalled()); firstController.abort(); resolveJson(savedState());
    expect(await first).toMatchObject({ stage: 'answer-read' }); expect(fetchMock).toHaveBeenCalledTimes(1);
    fetchMock.mockReset();
    let resolveChunks!: (response: Response) => void;
    fetchMock.mockResolvedValueOnce(jsonResponse(savedState())).mockResolvedValueOnce(jsonResponse(documentResponse()))
      .mockReturnValueOnce(new Promise(done => { resolveChunks = done; }));
    const secondController = new AbortController(), second = read(created, current, secondController.signal).catch(error => error);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3)); secondController.abort();
    resolveChunks(jsonResponse(documentChunks(DOCUMENT_ID)));
    expect(await second).toMatchObject({ stage: 'answer-read' }); expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('在来源等待期间公开元数据或创建依据变化，也不能返回已核对正文', async () => {
    const { created, current } = await setup();
    let resolve!: (response: Response) => void;
    fetchMock.mockResolvedValueOnce(jsonResponse(savedState()))
      .mockResolvedValueOnce(jsonResponse(documentResponse()))
      .mockResolvedValueOnce(jsonResponse(documentChunks(DOCUMENT_ID)))
      .mockResolvedValueOnce(jsonResponse(documentResponse(OTHER_DOCUMENT_ID)))
      .mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const action = read(created, current).catch(error => error);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    current.version += 1; created.question += '修改';
    resolve(jsonResponse(documentChunks(OTHER_DOCUMENT_ID)));
    expect(await action).toMatchObject({ stage: 'answer-read' });
    expect(fetchMock.mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true);
  });
});
