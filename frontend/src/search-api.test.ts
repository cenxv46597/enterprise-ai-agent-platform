import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError, SEARCH_UNCONFIRMED_MESSAGE, SearchUnconfirmedError, isSearchStrategy, searchKnowledgeBase,
  type SearchStrategy,
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
const HYBRID_HIT = { ...HIT, fusion_score: 2 / 61, vector_rank: 1, lexical_rank: 1 };
const HYBRID_RESULT = {
  ...RESULT, metric: 'reciprocal_rank_fusion', strategy: 'hybrid', results: [HYBRID_HIT],
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

const search = (query = '问题', signal = new AbortController().signal, strategy: SearchStrategy = 'vector') =>
  searchKnowledgeBase(WORKSPACE_ID, KNOWLEDGE_BASE_ID, query, signal, strategy);

function modeResult(strategy: SearchStrategy, results = [HIT]) {
  return strategy === 'vector' ? { ...RESULT, results } : {
    ...HYBRID_RESULT,
    results: results.map((hit, index) => ({
      ...hit, fusion_score: 1 / (61 + index), vector_rank: index + 1, lexical_rank: null,
    })),
  };
}

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
      strategy: 'vector', metric: 'cosine_distance',
      model: RESULT.model, dimensions: 1024,
      hits: [{
        chunkId: CHUNK_ID, documentId: DOCUMENT_ID, documentName: 'demo.md', chunkIndex: 2,
        content: '原文😀', startChar: 10, endChar: 13, distance: 0.25,
        fusionScore: null, vectorRank: null, lexicalRank: null,
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

  it('accepts only exact strategy names and rejects invalid runtime choices before any request', async () => {
    expect(isSearchStrategy('vector')).toBe(true);
    expect(isSearchStrategy('hybrid')).toBe(true);
    for (const strategy of ['', 'VECTOR', 'Hybrid', ' vector', 'hybrid ', PRIVATE, null, false, 1, [], {}]) {
      expect(isSearchStrategy(strategy)).toBe(false);
      const failure = await rejection(search('问题', new AbortController().signal, strategy as SearchStrategy));
      expect(failure).not.toBeInstanceOf(SearchUnconfirmedError);
      expect(failure.message).toBe('检索方式无效，请选择向量检索或混合检索。');
    }
    expect(isSearchStrategy(undefined)).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('keeps fused order when hybrid distances run backwards and projects absent channels as null', async () => {
    const sharedScore = Number((1 / 62 + 1 / 61).toFixed(12));
    const rows = [
      { ...HIT, distance: 0.9, fusion_score: sharedScore, vector_rank: 2, lexical_rank: 1, lexical_score: 3, raw: PRIVATE },
      { ...HIT, chunk_id: OTHER_CHUNK_ID, chunk_index: 3, distance: 0.1,
        fusion_score: 1 / 61, vector_rank: 1, lexical_rank: null, lexical_score: null },
      { ...HIT, chunk_id: THIRD_CHUNK_ID, document_id: OTHER_DOCUMENT_ID, document_name: 'other.md',
        distance: 0.7, fusion_score: 1 / 63, vector_rank: null, lexical_rank: 3, lexical_score: 2 },
    ];
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...HYBRID_RESULT, results: rows }))
      .mockResolvedValueOnce(jsonResponse(DOCUMENT))
      .mockResolvedValueOnce(jsonResponse({ ...DOCUMENT, id: OTHER_DOCUMENT_ID, original_filename: 'other.md' }));
    const result = await search('  TOKEN_EXPIRED  ', new AbortController().signal, 'hybrid');
    expect(result).toEqual({
      strategy: 'hybrid', metric: 'reciprocal_rank_fusion', model: RESULT.model, dimensions: 1024,
      hits: rows.map(row => ({
        chunkId: row.chunk_id, documentId: row.document_id, documentName: row.document_name,
        chunkIndex: row.chunk_index, content: row.content, startChar: row.start_char, endChar: row.end_char,
        distance: row.distance, fusionScore: row.fusion_score, vectorRank: row.vector_rank, lexicalRank: row.lexical_rank,
      })),
    });
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      query: 'TOKEN_EXPIRED', top_k: 3, strategy: 'hybrid', rerank: false,
    });
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      `${BASE_PATH}/search`, `${BASE_PATH}/documents/${DOCUMENT_ID}`, `${BASE_PATH}/documents/${OTHER_DOCUMENT_ID}`,
    ]);
    expect(JSON.stringify(result)).not.toContain(PRIVATE);
    expect(result.hits.every(hit => !Object.hasOwn(hit, 'lexicalScore'))).toBe(true);
  });

  it('keeps server order for tied fusion scores without sorting by distance or UUID in the client', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...HYBRID_RESULT, results: [
      { ...HYBRID_HIT, chunk_id: OTHER_CHUNK_ID, distance: 0.8, fusion_score: 1 / 61, vector_rank: 1, lexical_rank: null },
      { ...HYBRID_HIT, chunk_id: CHUNK_ID, distance: 0.1, fusion_score: 1 / 61, vector_rank: null, lexical_rank: 1 },
    ] })).mockResolvedValueOnce(jsonResponse(DOCUMENT));
    const result = await search('问题', new AbortController().signal, 'hybrid');
    expect(result.hits.map(hit => hit.chunkId)).toEqual([OTHER_CHUNK_ID, CHUNK_ID]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([{ model: null, dimensions: null }, { model: RESULT.model, dimensions: 1024 }])(
    'accepts empty hybrid candidates with the matching mode and profile %j without source reads', async profile => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...HYBRID_RESULT, ...profile, results: [] }));
      expect(await search('问题', new AbortController().signal, 'hybrid')).toEqual({
        strategy: 'hybrid', metric: 'reciprocal_rank_fusion', ...profile, hits: [],
      });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it.each<[SearchStrategy, unknown]>([
    ['vector', HYBRID_RESULT], ['hybrid', RESULT],
    ['vector', { ...RESULT, metric: 'reciprocal_rank_fusion' }],
    ['hybrid', { ...HYBRID_RESULT, metric: 'cosine_distance' }],
  ])('rejects a mismatched response for requested %s before reading sources', async (strategy, payload) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(payload));
    expect(await rejection(search('问题', new AbortController().signal, strategy))).toBeInstanceOf(SearchUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(['fusion_score', 'vector_rank', 'lexical_rank'] as const)(
    'rejects missing or invalid hybrid %s values before reading any source', async field => {
      const invalid = field === 'fusion_score'
        ? [undefined, null, true, '0.02', 0, -1, NaN, Infinity, -Infinity]
        : [undefined, true, '1', 0, -1, 21, 1.5, NaN, Infinity, -Infinity];
      for (const value of invalid) {
        const response = jsonResponse(HYBRID_RESULT);
        vi.spyOn(response, 'json').mockResolvedValueOnce({ ...HYBRID_RESULT, results: [{ ...HYBRID_HIT, [field]: value }] });
        fetchMock.mockResolvedValueOnce(response);
        expect(await rejection(search('问题', new AbortController().signal, 'hybrid'))).toBeInstanceOf(SearchUnconfirmedError);
      }
      expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
    },
  );

  it('rejects no-channel hits, inconsistent RRF scores and ascending fusion order without repairing the response', async () => {
    const invalid = [
      [{ ...HYBRID_HIT, vector_rank: null, lexical_rank: null }],
      [{ ...HYBRID_HIT, fusion_score: 1 / 61 }],
      [{ ...HYBRID_HIT, fusion_score: HYBRID_HIT.fusion_score + 1e-8 }],
      [{ ...HYBRID_HIT, fusion_score: 1 / 80, vector_rank: 20, lexical_rank: null },
        { ...HYBRID_HIT, chunk_id: OTHER_CHUNK_ID, fusion_score: 1 / 61, vector_rank: 1, lexical_rank: null }],
    ];
    for (const results of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...HYBRID_RESULT, results }));
      expect(await rejection(search('问题', new AbortController().signal, 'hybrid'))).toBeInstanceOf(SearchUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('preserves common hybrid response bounds and the no-reranking contract before source reads', async () => {
    for (const payload of [
      { ...HYBRID_RESULT, reranking: {} }, { ...HYBRID_RESULT, results: [HYBRID_HIT, HYBRID_HIT, HYBRID_HIT, HYBRID_HIT] },
      { ...HYBRID_RESULT, model: null, dimensions: null }, { ...HYBRID_RESULT, dimensions: 768 },
      { ...HYBRID_RESULT, results: [{ ...HYBRID_HIT, end_char: HIT.end_char + 1 }] },
      { ...HYBRID_RESULT, results: [HYBRID_HIT, HYBRID_HIT] },
    ]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      expect(await rejection(search('问题', new AbortController().signal, 'hybrid'))).toBeInstanceOf(SearchUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });

  it.each([
    ['vector', 'retrieval_rank'], ['vector', 'rerank_rank'],
    ['hybrid', 'retrieval_rank'], ['hybrid', 'rerank_rank'],
  ] as const)('rejects the unrequested %s hit marker %s before source GETs', async (strategy, field) => {
    const payload = modeResult(strategy);
    fetchMock.mockResolvedValueOnce(jsonResponse({
      ...payload, results: payload.results.map(hit => ({ ...hit, [field]: 1 })),
    }));
    expect(await rejection(search('问题', new AbortController().signal, strategy))).toBeInstanceOf(SearchUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(['vector', 'hybrid'] as const)('accepts explicit null reranking markers for a valid %s response', async strategy => {
    const payload = modeResult(strategy);
    fetchMock.mockResolvedValueOnce(jsonResponse({
      ...payload, results: payload.results.map(hit => ({ ...hit, retrieval_rank: null, rerank_rank: null })),
    })).mockResolvedValueOnce(jsonResponse(DOCUMENT));
    expect((await search('问题', new AbortController().signal, strategy)).hits).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('reports hybrid 422 as a possible input or capacity problem without inspecting details or retrying vector', async () => {
    const response = jsonResponse({ detail: PRIVATE }, 422);
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    const error = await rejection(search('问题', new AbortController().signal, 'hybrid'));
    expect(error).not.toBeInstanceOf(SearchUnconfirmedError);
    expect(error.status).toBe(422);
    expect(error.message).toContain('检索问题、请求参数或混合检索处理量可能不符合要求');
    expect(error.message).toContain('2000 个可检索文本块');
    expect(error.message).toContain('100 万正文字符');
    expect(error.message).toContain('不能确认是否已产生费用');
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string).strategy).toBe('hybrid');
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
      expect(await search()).toEqual({ strategy: 'vector', metric: 'cosine_distance', ...profile, hits: [] });
    }
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(['vector', 'hybrid'] as const)('verifies every distinct %s source once and waits for all documents', async strategy => {
    let resolveSecond!: (response: Response) => void;
    fetchMock.mockResolvedValueOnce(jsonResponse(modeResult(strategy, [
      HIT, { ...HIT, chunk_id: OTHER_CHUNK_ID, chunk_index: 3, distance: 0.25 },
      { ...HIT, chunk_id: THIRD_CHUNK_ID, document_id: OTHER_DOCUMENT_ID, distance: 2 },
    ]))).mockResolvedValueOnce(jsonResponse(DOCUMENT))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    let finished = false;
    const action = search('问题', new AbortController().signal, strategy).then((value) => { finished = true; return value; });
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

  it.each(['vector', 'hybrid'] as const)('rejects mismatched %s source identity, parent, filename or readiness as a whole', async strategy => {
    for (const document of [
      { ...DOCUMENT, id: OTHER_DOCUMENT_ID }, { ...DOCUMENT, knowledge_base_id: OTHER_DOCUMENT_ID },
      { ...DOCUMENT, original_filename: 'other.md' }, { ...DOCUMENT, status: 'parsed' },
      { ...DOCUMENT, status: 'failed' },
    ]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(modeResult(strategy))).mockResolvedValueOnce(jsonResponse(document));
      expect(await rejection(search('问题', new AbortController().signal, strategy))).toBeInstanceOf(SearchUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(10);
  });

  it.each([
    ['vector', 401], ['vector', 403], ['vector', 404], ['vector', 500], ['hybrid', 401], ['hybrid', 404],
  ] as const)('retains %s source GET HTTP %s without repeating search, and preserves only 401 as a session rejection', async (strategy, status) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(modeResult(strategy))).mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, status));
    const error = await rejection(search('问题', new AbortController().signal, strategy));
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

  it.each([
    ['vector', 'parent cancellation'], ['vector', '330-second timeout'],
    ['hybrid', 'parent cancellation'], ['hybrid', '330-second timeout'],
  ] as const)('does not accept late %s POST success or start source reads after %s', async (strategy, interruption) => {
    vi.useFakeTimers();
    const parent = new AbortController();
    let resolvePost!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise((resolve) => { resolvePost = resolve; }));
    const action = rejection(search('问题', parent.signal, strategy));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    if (interruption === 'parent cancellation') parent.abort();
    else {
      await vi.advanceTimersByTimeAsync(319_999);
      expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
    }
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    resolvePost(jsonResponse(modeResult(strategy)));
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

  it.each(['vector', 'hybrid'] as const)('rejects a late %s source response after cancellation and never reads another document', async strategy => {
    let resolveSource!: (response: Response) => void;
    fetchMock.mockResolvedValueOnce(jsonResponse(modeResult(strategy, [
      HIT, { ...HIT, chunk_id: OTHER_CHUNK_ID, document_id: OTHER_DOCUMENT_ID },
    ]))).mockImplementationOnce(() => new Promise((resolve) => { resolveSource = resolve; }));
    const parent = new AbortController();
    const action = rejection(search('问题', parent.signal, strategy));
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
