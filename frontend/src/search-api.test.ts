import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError, SEARCH_UNCONFIRMED_MESSAGE, SearchUnconfirmedError, searchKnowledgeBase,
} from './api';

const PRIVATE = 'PRIVATE-SEARCH-RESPONSE-DETAIL';
const WORKSPACE_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const KNOWLEDGE_BASE_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const DOCUMENT_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const OTHER_DOCUMENT_ID = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
const CHUNK_ID = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
const OTHER_CHUNK_ID = 'ffffffff-ffff-ffff-ffff-ffffffffffff';
const THIRD_CHUNK_ID = '11111111-1111-1111-1111-111111111111';
const BASE_PATH = `/api/v1/workspaces/${WORKSPACE_ID}/knowledge-bases/${KNOWLEDGE_BASE_ID}`;
const HIT = {
  chunk_id: CHUNK_ID, document_id: DOCUMENT_ID, document_name: 'demo.md', chunk_index: 2,
  content: '原文😀', start_char: 10, end_char: 13, distance: 0.25,
};
const RESULT = {
  metric: 'cosine_distance', strategy: 'vector', reranking: null,
  model: 'embedding-model', dimensions: 1024, results: [HIT],
};
const DOCUMENT = {
  id: DOCUMENT_ID, knowledge_base_id: KNOWLEDGE_BASE_ID, original_filename: 'demo.md', status: 'ready',
};
const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
}

async function rejection(action: Promise<unknown>): Promise<ApiError> {
  const error: unknown = await action.then(() => { throw new Error('Expected API rejection'); }, (failure: unknown) => failure);
  expect(error).toBeInstanceOf(ApiError);
  expect(String(error)).not.toContain(PRIVATE);
  return error as ApiError;
}

const search = (query = '问题', signal = new AbortController().signal) =>
  searchKnowledgeBase(WORKSPACE_ID, KNOWLEDGE_BASE_ID, query, signal);

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('scoped knowledge-base candidate search API', () => {
  it('sends one explicit query in the selected scope and verifies its source before returning public fields', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, raw: PRIVATE, results: [{
      ...HIT, chunk_id: CHUNK_ID.toUpperCase(), document_id: DOCUMENT_ID.toUpperCase(), raw: PRIVATE,
    }] })).mockResolvedValueOnce(jsonResponse(DOCUMENT));
    expect(await searchKnowledgeBase(
      WORKSPACE_ID.toUpperCase(), KNOWLEDGE_BASE_ID.toUpperCase(), '  问题😀\n', new AbortController().signal,
    )).toEqual({
      model: RESULT.model, dimensions: 1024,
      hits: [{
        chunkId: CHUNK_ID, documentId: DOCUMENT_ID, documentName: 'demo.md', chunkIndex: 2,
        content: '原文😀', startChar: 10, endChar: 13, distance: 0.25,
      }],
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE_PATH}/search`);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: 'POST', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
      body: JSON.stringify({ query: '问题😀', top_k: 3, strategy: 'vector', rerank: false }),
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

  it('rejects invalid scope IDs and blank or oversized queries before sending', async () => {
    for (const scope of [
      [`${WORKSPACE_ID}\n`, KNOWLEDGE_BASE_ID], [WORKSPACE_ID, 'not-a-uuid'],
    ]) {
      expect(await rejection(searchKnowledgeBase(scope[0], scope[1], '问题', new AbortController().signal)))
        .not.toBeInstanceOf(SearchUnconfirmedError);
    }
    for (const query of ['', ' \n\t ', '😀'.repeat(2001)]) {
      expect(await rejection(search(query))).toMatchObject({ message: '请输入 1 到 2000 字符的检索问题。' });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('counts query Unicode code points and accepts exactly 2000', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, results: [], model: null, dimensions: null }));
    expect((await search(`  ${'😀'.repeat(2000)}  `)).hits).toEqual([]);
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string).query).toBe('😀'.repeat(2000));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('accepts empty candidates with a null pair or valid profile without inventing source reads', async () => {
    for (const profile of [{ model: null, dimensions: null }, { model: RESULT.model, dimensions: 1024 }]) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, ...profile, results: [], reranking: undefined }));
      expect(await search()).toEqual({ ...profile, hits: [] });
    }
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('verifies each distinct document once and never resolves until every source is confirmed', async () => {
    let resolveSecond!: (response: Response) => void;
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, results: [
      HIT, { ...HIT, chunk_id: OTHER_CHUNK_ID, chunk_index: 3, distance: 0.25 },
      { ...HIT, chunk_id: THIRD_CHUNK_ID, document_id: OTHER_DOCUMENT_ID, distance: 2 },
    ] })).mockResolvedValueOnce(jsonResponse(DOCUMENT))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    let finished = false;
    const action = search().then((value) => { finished = true; return value; });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(finished).toBe(false);
    expect(fetchMock.mock.calls[1][0]).toBe(`${BASE_PATH}/documents/${DOCUMENT_ID}`);
    expect(fetchMock.mock.calls[2][0]).toBe(`${BASE_PATH}/documents/${OTHER_DOCUMENT_ID}`);
    resolveSecond(jsonResponse({ ...DOCUMENT, id: OTHER_DOCUMENT_ID }));
    expect((await action).hits).toHaveLength(3);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('rejects malformed mode, count and embedding profiles before reading any source', async () => {
    const invalid: unknown[] = [
      null, [], { ...RESULT, metric: 'cosine_similarity' }, { ...RESULT, strategy: 'hybrid' },
      { ...RESULT, reranking: {} }, { ...RESULT, results: null }, { ...RESULT, results: [HIT, HIT, HIT, HIT] },
      { ...RESULT, model: null, dimensions: null }, { ...RESULT, model: '' }, { ...RESULT, model: ' ' },
      { ...RESULT, model: '😀'.repeat(201) }, { ...RESULT, dimensions: '1024' }, { ...RESULT, dimensions: 768 },
      { ...RESULT, results: [], model: null, dimensions: 1024 },
      { ...RESULT, results: [], model: RESULT.model, dimensions: null },
      { ...RESULT, results: [], model: undefined, dimensions: undefined },
    ];
    for (const payload of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      expect(await rejection(search())).toBeInstanceOf(SearchUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, model: '😀'.repeat(200) }))
      .mockResolvedValueOnce(jsonResponse(DOCUMENT));
    expect((await search()).model).toBe('😀'.repeat(200));
  });

  it('rejects invalid hit identities, names, code-point ranges, numeric bounds and ordering before any source GET', async () => {
    const invalidHit: unknown[] = [
      null, { ...HIT, chunk_id: 'bad' }, { ...HIT, chunk_id: `${CHUNK_ID}\n` },
      { ...HIT, document_id: `${DOCUMENT_ID}\n` }, { ...HIT, document_name: '' },
      { ...HIT, document_name: '字'.repeat(256) }, { ...HIT, chunk_index: 0 },
      { ...HIT, chunk_index: 1.5 }, { ...HIT, chunk_index: '2' },
      { ...HIT, start_char: -1 }, { ...HIT, end_char: 10 }, { ...HIT, start_char: 1.5 },
      { ...HIT, end_char: '13' }, { ...HIT, end_char: 14 }, { ...HIT, content: '' },
      { ...HIT, content: ['原文'] }, { ...HIT, distance: -0.1 }, { ...HIT, distance: 2.1 },
      { ...HIT, distance: '0.25' }, { ...HIT, distance: true },
    ];
    for (const hit of invalidHit) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, results: [hit] }));
      expect(await rejection(search())).toBeInstanceOf(SearchUnconfirmedError);
    }
    for (const hits of [
      [HIT, HIT],
      [HIT, { ...HIT, chunk_id: OTHER_CHUNK_ID, document_name: 'different.md' }],
      [HIT, { ...HIT, chunk_id: OTHER_CHUNK_ID, distance: 0.1 }],
    ]) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, results: hits }));
      expect(await rejection(search())).toBeInstanceOf(SearchUnconfirmedError);
    }
    for (const distance of [NaN, Infinity, -Infinity]) {
      const response = jsonResponse(RESULT);
      vi.spyOn(response, 'json').mockResolvedValueOnce({ ...RESULT, results: [{ ...HIT, distance }] });
      fetchMock.mockResolvedValueOnce(response);
      expect(await rejection(search())).toBeInstanceOf(SearchUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalidHit.length + 6);
  });

  it('bounds the total accepted body to 100000 Unicode code points rather than UTF-16 units', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, results: [{
      ...HIT, content: '😀'.repeat(100_000), start_char: 0, end_char: 100_000, distance: 0,
    }] })).mockResolvedValueOnce(jsonResponse(DOCUMENT));
    expect((await search()).hits[0].content).toHaveLength(200_000);
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, results: [
      { ...HIT, content: '字'.repeat(60_000), start_char: 0, end_char: 60_000 },
      { ...HIT, chunk_id: OTHER_CHUNK_ID, content: '字'.repeat(40_001), start_char: 0, end_char: 40_001 },
    ] }));
    expect(await rejection(search())).toBeInstanceOf(SearchUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it.each([401, 403, 404, 409, 422])('maps POST HTTP %s to a fixed safe message without reading private errors or retrying', async (status) => {
    const response = jsonResponse({ detail: PRIVATE }, status);
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    const error = await rejection(search());
    expect(error).not.toBeInstanceOf(SearchUnconfirmedError);
    expect(error.status).toBe(status);
    if (status === 401) expect(error.message).toBe('登录已失效，请重新登录。');
    if (status === 409 || status === 422) expect(error.message).toContain('不能确认是否已产生费用');
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('keeps unknown HTTP status, malformed JSON and network failure unconfirmed and never retries the POST', async () => {
    for (const status of [201, 202, 204, 400, 429, 500, 502, 504]) {
      fetchMock.mockResolvedValueOnce(status === 204 ? new Response(null, { status }) : jsonResponse({ detail: PRIVATE }, status));
      expect(await rejection(search())).toMatchObject({ name: 'SearchUnconfirmedError', status });
    }
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE));
    expect(await rejection(search())).toBeInstanceOf(SearchUnconfirmedError);
    fetchMock.mockRejectedValueOnce(new Error(PRIVATE));
    expect(await rejection(search())).toBeInstanceOf(SearchUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(10);
  });

  it('rejects mismatched document identity, parent, filename or readiness and returns no partial result', async () => {
    for (const document of [
      { ...DOCUMENT, id: OTHER_DOCUMENT_ID }, { ...DOCUMENT, knowledge_base_id: OTHER_DOCUMENT_ID },
      { ...DOCUMENT, original_filename: 'other.md' }, { ...DOCUMENT, status: 'parsed' },
      { ...DOCUMENT, status: 'failed' },
    ]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(RESULT)).mockResolvedValueOnce(jsonResponse(document));
      expect(await rejection(search())).toBeInstanceOf(SearchUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(10);
  });

  it.each([401, 403, 404, 500])('retains source GET HTTP %s, only preserves 401 as a session rejection and never repeats search', async (status) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(RESULT)).mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, status));
    const error = await rejection(search());
    expect(error.status).toBe(status);
    if (status === 401) {
      expect(error).not.toBeInstanceOf(SearchUnconfirmedError);
      expect(error.message).toBe('登录已失效，请重新登录。');
    } else {
      expect(error).toBeInstanceOf(SearchUnconfirmedError);
      expect(error.message).toBe(SEARCH_UNCONFIRMED_MESSAGE);
    }
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not send any request when already cancelled', async () => {
    const parent = new AbortController();
    parent.abort();
    expect(await rejection(search('问题', parent.signal))).toBeInstanceOf(SearchUnconfirmedError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['parent cancellation', '330-second timeout'] as const)('does not accept late POST success or start source reads after %s', async (interruption) => {
    vi.useFakeTimers();
    const parent = new AbortController();
    let resolvePost!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise((resolve) => { resolvePost = resolve; }));
    const action = rejection(search('问题', parent.signal));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    if (interruption === 'parent cancellation') parent.abort();
    else {
      await vi.advanceTimersByTimeAsync(319_999);
      expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
    }
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    resolvePost(jsonResponse(RESULT));
    expect(await action).toBeInstanceOf(SearchUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('rejects a late JSON body after cancellation before source reads', async () => {
    let resolveBody!: (payload: unknown) => void;
    const response = jsonResponse(RESULT);
    vi.spyOn(response, 'json').mockReturnValueOnce(new Promise((resolve) => { resolveBody = resolve; }));
    fetchMock.mockResolvedValueOnce(response);
    const parent = new AbortController();
    const action = rejection(search('问题', parent.signal));
    await Promise.resolve();
    parent.abort();
    resolveBody(RESULT);
    expect(await action).toBeInstanceOf(SearchUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('rejects a late source response after cancellation and never reads the next document', async () => {
    let resolveSource!: (response: Response) => void;
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, results: [
      HIT, { ...HIT, chunk_id: OTHER_CHUNK_ID, document_id: OTHER_DOCUMENT_ID },
    ] })).mockImplementationOnce(() => new Promise((resolve) => { resolveSource = resolve; }));
    const parent = new AbortController();
    const action = rejection(search('问题', parent.signal));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    parent.abort();
    resolveSource(jsonResponse(DOCUMENT));
    expect(await action).toBeInstanceOf(SearchUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('uses a fresh ten-second timeout for source GETs after the independent 330-second POST wait', async () => {
    vi.useFakeTimers();
    let resolvePost!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise((resolve) => { resolvePost = resolve; }))
      .mockImplementationOnce((_path, options) => new Promise((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
      }));
    const parent = new AbortController();
    const removeListener = vi.spyOn(parent.signal, 'removeEventListener');
    const action = rejection(search('问题', parent.signal));
    await vi.advanceTimersByTimeAsync(329_999);
    resolvePost(jsonResponse(RESULT));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(9_999);
    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await action).toBeInstanceOf(SearchUnconfirmedError);
    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
    expect(removeListener).toHaveBeenCalledWith('abort', expect.any(Function));
  });
});
