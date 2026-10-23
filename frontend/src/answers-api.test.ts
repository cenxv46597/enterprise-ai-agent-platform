import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ANSWER_UNCONFIRMED_MESSAGE, AnswerUnconfirmedError, ApiError, answerKnowledgeBase,
  type AnswerTopK, type SearchStrategy,
} from './api';

const PRIVATE = 'PRIVATE-ANSWER-RESPONSE-DETAIL';
const TRACE = 'a0e0b901-2b0d-4ae0-812a-bab2cf9e7b01';
const WORKSPACE_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const KNOWLEDGE_BASE_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const DOCUMENT_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const OTHER_DOCUMENT_ID = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
const CHUNK_ID = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
const OTHER_CHUNK_ID = 'ffffffff-ffff-ffff-ffff-ffffffffffff';
const BASE_PATH = `/api/v1/workspaces/${WORKSPACE_ID}/knowledge-bases/${KNOWLEDGE_BASE_ID}`;
const CITATION = {
  chunk_id: CHUNK_ID, document_id: DOCUMENT_ID, document_name: 'demo.md', chunk_index: 2,
  quote: '完整引文😀', start_char: 10, end_char: 15,
};
const RESULT = { answer: '这是基于资料的回答。', abstained: false, citations: [CITATION], reranking: null };
const DOCUMENT = {
  id: DOCUMENT_ID, knowledge_base_id: KNOWLEDGE_BASE_ID, original_filename: 'demo.md', status: 'ready',
};
const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(payload: unknown, status = 200, traceId?: string): Response {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (traceId) headers.set('X-Trace-Id', traceId);
  return new Response(JSON.stringify(payload), { status, headers });
}

async function rejection(action: Promise<unknown>): Promise<ApiError> {
  const error: unknown = await action.then(() => { throw new Error('Expected API rejection'); }, (failure: unknown) => failure);
  expect(error).toBeInstanceOf(ApiError);
  expect(String(error)).not.toContain(PRIVATE);
  return error as ApiError;
}

const ask = (question = '问题', signal = new AbortController().signal, strategy: SearchStrategy = 'vector') =>
  answerKnowledgeBase(WORKSPACE_ID, KNOWLEDGE_BASE_ID, question, signal, strategy);

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('scoped knowledge-base cited answer API', () => {
  it.each(['vector', 'hybrid'] as const)('sends one explicit %s answer request and verifies sources without inventing response strategy metadata', async strategy => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, raw: PRIVATE, citations: [{
      ...CITATION, chunk_id: CHUNK_ID.toUpperCase(), document_id: DOCUMENT_ID.toUpperCase(), raw: PRIVATE,
    }] })).mockResolvedValueOnce(jsonResponse(DOCUMENT));
    expect(await answerKnowledgeBase(
      WORKSPACE_ID.toUpperCase(), KNOWLEDGE_BASE_ID.toUpperCase(), '  问题😀\n', new AbortController().signal,
      strategy === 'vector' ? undefined : strategy,
    )).toEqual({
      answer: RESULT.answer, abstained: false,
      citations: [{
        chunkId: CHUNK_ID, documentId: DOCUMENT_ID, documentName: 'demo.md', chunkIndex: 2,
        quote: CITATION.quote, startChar: 10, endChar: 15,
      }],
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE_PATH}/answers`);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: 'POST', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
      body: JSON.stringify({ question: '问题😀', top_k: 3, strategy, rerank: false }),
    });
    expect(fetchMock.mock.calls[1][0]).toBe(`${BASE_PATH}/documents/${DOCUMENT_ID}`);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: 'GET', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
    });
    for (const [, options] of fetchMock.mock.calls) {
      const headers = new Headers(options?.headers);
      expect(headers.get('X-EAAP-Web')).toBe('1');
      expect(headers.has('Authorization')).toBe(false);
      expect(headers.has('Cookie')).toBe(false);
      expect(options?.signal).toBeInstanceOf(AbortSignal);
    }
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('Content-Type')).toBe('application/json');
  });

  it('rejects invalid scope IDs and blank or oversized questions before sending', async () => {
    for (const scope of [[`${WORKSPACE_ID}\n`, KNOWLEDGE_BASE_ID], [WORKSPACE_ID, 'not-a-uuid']]) {
      expect(await rejection(answerKnowledgeBase(scope[0], scope[1], '问题', new AbortController().signal)))
        .not.toBeInstanceOf(AnswerUnconfirmedError);
    }
    for (const question of ['', ' \n\t ', '😀'.repeat(2001)]) {
      expect(await rejection(ask(question))).toMatchObject({ message: '请输入 1 到 2000 字符的问答问题。' });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects invalid retrieval strategies before sending and exposes only a fixed safe message', async () => {
    for (const strategy of ['', 'VECTOR', 'Hybrid', ' vector', 'hybrid ', PRIVATE, null, false, 1, [], {}]) {
      const failure = await rejection(ask('问题', new AbortController().signal, strategy as SearchStrategy));
      expect(failure).not.toBeInstanceOf(AnswerUnconfirmedError);
      expect(failure.message).toBe('检索方式无效，请选择向量检索或混合检索。');
      expect(failure.traceId).toBeNull();
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('sends a chosen five-candidate answer request and rejects unsupported counts before sending', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ answer: '证据不足。', abstained: true, citations: [] }));
    await answerKnowledgeBase(WORKSPACE_ID, KNOWLEDGE_BASE_ID, '问题', new AbortController().signal, 'hybrid', 5);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      question: '问题', top_k: 5, strategy: 'hybrid', rerank: false,
    });
    for (const count of [0, 1, 4, 6, '5', null, true, 3.5]) {
      const failure = await rejection(answerKnowledgeBase(
        WORKSPACE_ID, KNOWLEDGE_BASE_ID, '问题', new AbortController().signal, 'vector', count as AnswerTopK));
      expect(failure.message).toBe('问答候选数量无效，请选择 3 或 5 段。');
    }
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('counts questions by Unicode code points and accepts exactly 2000 with a normal abstention', async () => {
    const result = { answer: '目前资料不足。', abstained: true, citations: [] };
    fetchMock.mockResolvedValueOnce(jsonResponse(result));
    expect(await ask(`  ${'😀'.repeat(2000)}  `)).toEqual(result);
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string).question).toBe('😀'.repeat(2000));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(['vector', 'hybrid'] as const)('accepts a normal %s abstention without source reads or added response metadata', async strategy => {
    for (const reranking of [null, undefined]) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ answer: '资料不足以确认答案。', abstained: true, citations: [], reranking }));
      expect(await ask('问题', new AbortController().signal, strategy)).toEqual({ answer: '资料不足以确认答案。', abstained: true, citations: [] });
    }
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(['vector', 'hybrid'] as const)('allows different full %s quotes and waits for every distinct source document', async strategy => {
    let resolveSecond!: (response: Response) => void;
    const secondQuote = { ...CITATION, quote: '第二句', start_char: 30, end_char: 33 };
    const otherSource = { ...CITATION, chunk_id: OTHER_CHUNK_ID, document_id: OTHER_DOCUMENT_ID };
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, citations: [CITATION, secondQuote, otherSource] }))
      .mockResolvedValueOnce(jsonResponse(DOCUMENT))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    let finished = false;
    const action = ask('问题', new AbortController().signal, strategy).then((value) => { finished = true; return value; });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(finished).toBe(false);
    expect(fetchMock.mock.calls[1][0]).toBe(`${BASE_PATH}/documents/${DOCUMENT_ID}`);
    expect(fetchMock.mock.calls[2][0]).toBe(`${BASE_PATH}/documents/${OTHER_DOCUMENT_ID}`);
    resolveSecond(jsonResponse({ ...DOCUMENT, id: OTHER_DOCUMENT_ID }));
    expect((await action).citations.map((citation) => citation.quote)).toEqual([
      CITATION.quote, secondQuote.quote, otherSource.quote,
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('accepts twenty distinct quotations even though the fixed retrieval top_k is three', async () => {
    const citations = Array.from({ length: 20 }, (_, index) => ({
      ...CITATION, quote: `引文${index}`, start_char: 0, end_char: Array.from(`引文${index}`).length,
    }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, citations, reranking: undefined }))
      .mockResolvedValueOnce(jsonResponse(DOCUMENT));
    expect((await ask()).citations).toHaveLength(20);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('preserves full answers and quotes at Unicode limits without the search preview truncation', async () => {
    const name = '😀'.repeat(255);
    const quote = '😀'.repeat(1000);
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, answer: '😀'.repeat(4000), citations: [{
      ...CITATION, document_name: name, quote, start_char: 0, end_char: 1000,
    }] })).mockResolvedValueOnce(jsonResponse({ ...DOCUMENT, original_filename: name }));
    const result = await ask();
    expect(result.answer).toBe('😀'.repeat(4000));
    expect(result.citations[0].quote).toBe(quote);
    expect(result.citations[0].documentName).toBe(name);
  });

  it('rejects malformed answers, decisions, citation counts and reranking traces before reading sources', async () => {
    const invalid: unknown[] = [
      null, [], {}, { ...RESULT, answer: '' }, { ...RESULT, answer: ' \n\t' },
      { ...RESULT, answer: ['答案'] }, { ...RESULT, answer: '😀'.repeat(4001) },
      { ...RESULT, abstained: 0 }, { ...RESULT, abstained: 'false' },
      { ...RESULT, abstained: true }, { ...RESULT, citations: [] }, { ...RESULT, citations: null },
      { ...RESULT, citations: Array.from({ length: 21 }, () => CITATION) },
      { ...RESULT, reranking: {} }, { ...RESULT, reranking: false },
      { ...RESULT, reranking: { enabled: false } },
      { answer: '', abstained: true, citations: [] },
    ];
    for (const payload of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      expect(await rejection(ask())).toBeInstanceOf(AnswerUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('rejects invalid citation identities, names, quote bounds and code-point ranges before any source GET', async () => {
    const invalid: unknown[] = [
      null, { ...CITATION, chunk_id: 'bad' }, { ...CITATION, chunk_id: `${CHUNK_ID}\n` },
      { ...CITATION, document_id: `${DOCUMENT_ID}\n` }, { ...CITATION, document_name: '' },
      { ...CITATION, document_name: '字'.repeat(256) }, { ...CITATION, chunk_index: 0 },
      { ...CITATION, chunk_index: true }, { ...CITATION, chunk_index: 1.5 },
      { ...CITATION, chunk_index: Number.MAX_SAFE_INTEGER + 1 }, { ...CITATION, chunk_index: '2' },
      { ...CITATION, quote: '' }, { ...CITATION, quote: '\n\t ' }, { ...CITATION, quote: ['引文'] },
      { ...CITATION, quote: '😀'.repeat(1001), start_char: 0, end_char: 1001 },
      { ...CITATION, start_char: -1 }, { ...CITATION, start_char: 1.5 },
      { ...CITATION, start_char: true }, { ...CITATION, end_char: 10 },
      { ...CITATION, end_char: '15' }, { ...CITATION, end_char: 16 },
      { ...CITATION, end_char: Number.MAX_SAFE_INTEGER + 1 },
    ];
    for (const citation of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, citations: [citation] }));
      expect(await rejection(ask())).toBeInstanceOf(AnswerUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('rejects repeated chunk-quote pairs and contradictory document or chunk identities', async () => {
    for (const citations of [
      [CITATION, { ...CITATION, chunk_id: CHUNK_ID.toUpperCase() }],
      [CITATION, { ...CITATION, chunk_id: OTHER_CHUNK_ID, document_name: 'different.md' }],
      [CITATION, { ...CITATION, document_id: OTHER_DOCUMENT_ID, quote: '第二句', start_char: 0, end_char: 3 }],
      [CITATION, { ...CITATION, chunk_index: 3, quote: '第二句', start_char: 0, end_char: 3 }],
    ]) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, citations }));
      expect(await rejection(ask())).toBeInstanceOf(AnswerUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it.each([
    ['vector', 401], ['vector', 403], ['vector', 404], ['vector', 409], ['vector', 422], ['hybrid', 401],
  ] as const)('maps %s POST HTTP %s to safe messages and trace IDs without reading errors or retrying', async (strategy, status) => {
    const response = jsonResponse({ detail: PRIVATE }, status, TRACE);
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    const error = await rejection(ask('问题', new AbortController().signal, strategy));
    expect(error).not.toBeInstanceOf(AnswerUnconfirmedError);
    expect(error.status).toBe(status);
    expect(error.traceId).toBe(TRACE);
    if (status === 401) expect(error.message).toBe('登录已失效，请重新登录。');
    if (status === 409 || status === 422) expect(error.message).toContain('不能确认是否已产生费用');
    if (status === 422) expect(error.message).toBe('请核对问答问题、请求参数与上下文预算；本响应不能确认是否已产生费用。');
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('keeps hybrid 422 causes and cost uncertain without reading detail or automatically resending vector', async () => {
    const response = jsonResponse({ detail: PRIVATE }, 422, TRACE);
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    const error = await rejection(ask('问题', new AbortController().signal, 'hybrid'));
    expect(error).not.toBeInstanceOf(AnswerUnconfirmedError);
    expect(error.status).toBe(422);
    expect(error.traceId).toBe(TRACE);
    expect(error.message).toContain('问答问题、请求参数、上下文预算或混合检索处理量可能不符合要求');
    expect(error.message).toContain('2000 个可检索文本块');
    expect(error.message).toContain('100 万正文字符');
    expect(error.message).toContain('不能确认是否已产生费用');
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE_PATH}/answers`);
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      question: '问题', top_k: 3, strategy: 'hybrid', rerank: false,
    });
  });

  it('keeps unknown statuses, malformed JSON and network failures unconfirmed without retrying', async () => {
    for (const status of [201, 202, 204, 400, 429, 500, 502, 503, 504]) {
      fetchMock.mockResolvedValueOnce(status === 204 ? new Response(null, { status }) : jsonResponse({ detail: PRIVATE }, status));
      expect(await rejection(ask())).toMatchObject({ name: 'AnswerUnconfirmedError', status });
    }
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE));
    expect(await rejection(ask())).toBeInstanceOf(AnswerUnconfirmedError);
    fetchMock.mockRejectedValueOnce(new Error(PRIVATE));
    expect(await rejection(ask())).toBeInstanceOf(AnswerUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(11);
  });

  it.each(['vector', 'hybrid'] as const)('rejects mismatched %s source identity, parent, name, readiness or status as a whole', async strategy => {
    for (const document of [
      { ...DOCUMENT, id: OTHER_DOCUMENT_ID }, { ...DOCUMENT, knowledge_base_id: OTHER_DOCUMENT_ID },
      { ...DOCUMENT, original_filename: 'other.md' }, { ...DOCUMENT, status: 'parsed' },
      { ...DOCUMENT, status: 'failed' },
    ]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(RESULT)).mockResolvedValueOnce(jsonResponse(document));
      expect(await rejection(ask('问题', new AbortController().signal, strategy))).toBeInstanceOf(AnswerUnconfirmedError);
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(RESULT)).mockResolvedValueOnce(jsonResponse(DOCUMENT, 201));
    expect(await rejection(ask('问题', new AbortController().signal, strategy))).toBeInstanceOf(AnswerUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(12);
  });

  it.each([
    ['vector', 401], ['vector', 403], ['vector', 404], ['vector', 500], ['hybrid', 401], ['hybrid', 404],
  ] as const)('retains %s source GET HTTP %s and its trace, preserving only 401 as session rejection', async (strategy, status) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(RESULT)).mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, status, TRACE));
    const error = await rejection(ask('问题', new AbortController().signal, strategy));
    expect(error.status).toBe(status);
    expect(error.traceId).toBe(TRACE);
    if (status === 401) {
      expect(error).not.toBeInstanceOf(AnswerUnconfirmedError);
      expect(error.message).toBe('登录已失效，请重新登录。');
    } else {
      expect(error).toBeInstanceOf(AnswerUnconfirmedError);
      expect(error.message).toBe(ANSWER_UNCONFIRMED_MESSAGE);
    }
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(['vector', 'hybrid'] as const)('rejects the whole %s answer when a later source fails after the first passed', async strategy => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, citations: [CITATION, {
      ...CITATION, chunk_id: OTHER_CHUNK_ID, document_id: OTHER_DOCUMENT_ID,
    }] })).mockResolvedValueOnce(jsonResponse(DOCUMENT))
      .mockResolvedValueOnce(jsonResponse({ ...DOCUMENT, id: OTHER_DOCUMENT_ID, status: 'parsed' }));
    expect(await rejection(ask('问题', new AbortController().signal, strategy))).toBeInstanceOf(AnswerUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('does not send requests when already cancelled', async () => {
    const parent = new AbortController();
    parent.abort();
    expect(await rejection(ask('问题', parent.signal))).toBeInstanceOf(AnswerUnconfirmedError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ['vector', 'parent cancellation'], ['vector', '630-second timeout'],
    ['hybrid', 'parent cancellation'], ['hybrid', '630-second timeout'],
  ] as const)('rejects late %s POST success after %s without source reads', async (strategy, interruption) => {
    vi.useFakeTimers();
    const parent = new AbortController();
    let resolvePost!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise((resolve) => { resolvePost = resolve; }));
    const action = rejection(ask('问题', parent.signal, strategy));
    await vi.advanceTimersByTimeAsync(330_000);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    if (interruption === 'parent cancellation') parent.abort();
    else {
      await vi.advanceTimersByTimeAsync(299_999);
      expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
    }
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    resolvePost(jsonResponse(RESULT));
    expect(await action).toBeInstanceOf(AnswerUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(['vector', 'hybrid'] as const)('rejects a late %s JSON body after cancellation before source reads', async strategy => {
    let resolveBody!: (payload: unknown) => void;
    const response = jsonResponse(RESULT);
    const read = vi.spyOn(response, 'json').mockReturnValueOnce(new Promise((resolve) => { resolveBody = resolve; }));
    fetchMock.mockResolvedValueOnce(response);
    const parent = new AbortController();
    const action = rejection(ask('问题', parent.signal, strategy));
    await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    parent.abort();
    resolveBody(RESULT);
    expect(await action).toBeInstanceOf(AnswerUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(['vector', 'hybrid'] as const)('rejects a late %s source response after cancellation and skips later documents', async strategy => {
    let resolveSource!: (response: Response) => void;
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, citations: [CITATION, {
      ...CITATION, chunk_id: OTHER_CHUNK_ID, document_id: OTHER_DOCUMENT_ID,
    }] })).mockImplementationOnce(() => new Promise((resolve) => { resolveSource = resolve; }));
    const parent = new AbortController();
    const action = rejection(ask('问题', parent.signal, strategy));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    parent.abort();
    resolveSource(jsonResponse(DOCUMENT));
    expect(await action).toBeInstanceOf(AnswerUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('uses a fresh ten-second source timeout after the independent 630-second POST wait and cleans listeners', async () => {
    vi.useFakeTimers();
    let resolvePost!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise((resolve) => { resolvePost = resolve; }))
      .mockImplementationOnce((_path, options) => new Promise((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
      }));
    const parent = new AbortController();
    const removeListener = vi.spyOn(parent.signal, 'removeEventListener');
    const action = rejection(ask('问题', parent.signal));
    await vi.advanceTimersByTimeAsync(629_999);
    resolvePost(jsonResponse(RESULT));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(9_999);
    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await action).toBeInstanceOf(AnswerUnconfirmedError);
    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
    expect(removeListener).toHaveBeenCalledWith('abort', expect.any(Function));
  });
});
