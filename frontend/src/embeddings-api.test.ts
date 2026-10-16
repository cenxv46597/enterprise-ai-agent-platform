import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError, EMBEDDING_UNCONFIRMED_MESSAGE, EmbeddingUnconfirmedError, createDocumentEmbeddings,
  listDocumentChunks,
} from './api';

const PRIVATE = 'PRIVATE-EMBEDDING-RESPONSE-DETAIL';
const WORKSPACE_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const KNOWLEDGE_BASE_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const DOCUMENT_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const OTHER_ID = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
const DOCUMENT_PATH = `/api/v1/workspaces/${WORKSPACE_ID}/knowledge-bases/${KNOWLEDGE_BASE_ID}/documents/${DOCUMENT_ID}`;
const RESULT = {
  document_id: DOCUMENT_ID, status: 'ready', model: 'embedding-model', dimensions: 1024,
  chunk_count: 5, reused: false,
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

const embed = (budget = 5, signal = new AbortController().signal) =>
  createDocumentEmbeddings(WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, budget, signal);

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('explicit-budget document embeddings API', () => {
  it('posts exactly one explicit scoped JSON budget with the browser cookie boundary and returns only public fields', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, document_id: DOCUMENT_ID.toUpperCase(), raw: PRIVATE }));
    expect(await createDocumentEmbeddings(
      WORKSPACE_ID.toUpperCase(), KNOWLEDGE_BASE_ID.toUpperCase(), DOCUMENT_ID.toUpperCase(), 5, new AbortController().signal,
    )).toEqual({
      documentId: DOCUMENT_ID, status: 'ready', model: RESULT.model, dimensions: 1024, chunkCount: 5, reused: false,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe(`${DOCUMENT_PATH}/embeddings`);
    expect(options).toMatchObject({
      method: 'POST', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
      body: JSON.stringify({ max_chunks: 5 }),
    });
    expect(options?.signal).toBeInstanceOf(AbortSignal);
    const headers = new Headers(options?.headers);
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('X-EAAP-Web')).toBe('1');
    expect(headers.has('Authorization')).toBe(false);
    expect(headers.has('Cookie')).toBe(false);
  });

  it('rejects all invalid scope IDs and missing, fractional, coercible or out-of-range budgets before sending', async () => {
    const invalidId = `${DOCUMENT_ID}\n`;
    for (const args of [
      [invalidId, KNOWLEDGE_BASE_ID, DOCUMENT_ID], [WORKSPACE_ID, invalidId, DOCUMENT_ID],
      [WORKSPACE_ID, KNOWLEDGE_BASE_ID, invalidId],
    ]) {
      const error = await rejection(createDocumentEmbeddings(args[0], args[1], args[2], 5, new AbortController().signal));
      expect(error).not.toBeInstanceOf(EmbeddingUnconfirmedError);
    }
    for (const budget of [undefined, null, '5', true, NaN, Infinity, -1, 0, 1.5, 1001]) {
      const error = await rejection(createDocumentEmbeddings(
        WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, budget as number, new AbortController().signal,
      ));
      expect(error).not.toBeInstanceOf(EmbeddingUnconfirmedError);
      expect(error.message).toContain('1 到 1000 的整数');
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('accepts both budget endpoints without expanding the requested allowance', async () => {
    for (const budget of [1, 1000]) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, chunk_count: budget }));
      expect((await embed(budget)).chunkCount).toBe(budget);
      expect(fetchMock.mock.lastCall?.[1]?.body).toBe(JSON.stringify({ max_chunks: budget }));
    }
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('accepts a compatible reused result exceeding the new-generation budget without inferring zero cost', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, chunk_count: 10_000, reused: true }));
    expect(await embed(1)).toEqual({
      documentId: DOCUMENT_ID, status: 'ready', model: RESULT.model, dimensions: 1024, chunkCount: 10_000, reused: true,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('rejects untrusted success identity, status, model, dimensions, counts, flags and new generation above budget', async () => {
    const invalid: unknown[] = [
      null, [], { ...RESULT, document_id: OTHER_ID }, { ...RESULT, document_id: `${DOCUMENT_ID}\n` },
      { ...RESULT, status: 'parsed' }, { ...RESULT, dimensions: '1024' }, { ...RESULT, dimensions: 768 },
      { ...RESULT, model: '' }, { ...RESULT, model: ' ' }, { ...RESULT, model: null },
      { ...RESULT, model: '😀'.repeat(201) }, { ...RESULT, chunk_count: 0 }, { ...RESULT, chunk_count: -1 },
      { ...RESULT, chunk_count: 1.5 }, { ...RESULT, chunk_count: '5' },
      { ...RESULT, chunk_count: 10_001, reused: true }, { ...RESULT, reused: 'false' },
      { ...RESULT, reused: 1 }, { ...RESULT, chunk_count: 6 },
    ];
    for (const payload of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      const error = await rejection(embed());
      expect(error).toBeInstanceOf(EmbeddingUnconfirmedError);
      expect(error.message).toBe(EMBEDDING_UNCONFIRMED_MESSAGE);
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...RESULT, model: '😀'.repeat(200) }));
    expect((await embed()).model).toBe('😀'.repeat(200));
  });

  it.each([
    [401, '登录已失效，请重新登录。'],
    [403, '向量化请求被拒绝，请检查登录与浏览器来源。'],
    [404, '该文档不存在或你已无权访问，请返回文档列表。'],
    [409, '当前文档状态或向量配置不允许完成本次操作，请刷新文档与切块核对状态；本响应不能确认是否已产生费用。'],
    [422, '请核对向量化参数、已有块数与本次新生成上限；本响应不能确认是否已产生费用。'],
  ] as const)('maps HTTP %s to a safe rejection without reading backend details or repeating the mutation', async (status, message) => {
    const response = jsonResponse({ detail: PRIVATE }, status);
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    const error = await rejection(embed());
    expect(error).not.toBeInstanceOf(EmbeddingUnconfirmedError);
    expect(error).toMatchObject({ status, message });
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('keeps other HTTP responses, malformed JSON and network failures unconfirmed without retrying', async () => {
    for (const status of [201, 202, 204, 400, 429, 500, 502, 504]) {
      fetchMock.mockResolvedValueOnce(status === 204 ? new Response(null, { status }) : jsonResponse({ detail: PRIVATE }, status));
      const error = await rejection(embed());
      expect(error).toBeInstanceOf(EmbeddingUnconfirmedError);
      expect(error).toMatchObject({ message: EMBEDDING_UNCONFIRMED_MESSAGE, status });
    }
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE));
    expect(await rejection(embed())).toBeInstanceOf(EmbeddingUnconfirmedError);
    fetchMock.mockRejectedValueOnce(new Error(PRIVATE));
    expect(await rejection(embed())).toBeInstanceOf(EmbeddingUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(10);
  });

  it('does not send a mutation when the parent is already cancelled', async () => {
    const parent = new AbortController();
    parent.abort();
    const error = await rejection(embed(5, parent.signal));
    expect(error).not.toBeInstanceOf(EmbeddingUnconfirmedError);
    expect(error.message).toBe('请求已取消。');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['parent cancellation', '330-second timeout'] as const)('keeps a sent mutation unconfirmed after %s and clears its timer/listener', async (interruption) => {
    vi.useFakeTimers();
    const parent = new AbortController();
    const removeListener = vi.spyOn(parent.signal, 'removeEventListener');
    fetchMock.mockImplementationOnce((_path, options) => new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
    }));
    const result = rejection(embed(5, parent.signal));
    await vi.advanceTimersByTimeAsync(10_000);
    const sentSignal = fetchMock.mock.calls[0][1]?.signal;
    expect(sentSignal?.aborted).toBe(false);
    if (interruption === 'parent cancellation') parent.abort();
    else {
      await vi.advanceTimersByTimeAsync(319_999);
      expect(sentSignal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
    }
    const error = await result;
    expect(error).toBeInstanceOf(EmbeddingUnconfirmedError);
    expect(error.message).toBe(EMBEDDING_UNCONFIRMED_MESSAGE);
    expect(sentSignal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(removeListener).toHaveBeenCalledWith('abort', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cleans up the long timer after success and preserves the ten-second timeout of ordinary reads', async () => {
    vi.useFakeTimers();
    const parent = new AbortController();
    const removeListener = vi.spyOn(parent.signal, 'removeEventListener');
    fetchMock.mockResolvedValueOnce(jsonResponse(RESULT));
    await embed(5, parent.signal);
    expect(vi.getTimerCount()).toBe(0);
    expect(removeListener).toHaveBeenCalledWith('abort', expect.any(Function));
    fetchMock.mockImplementationOnce((_path, options) => new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
    }));
    const result = rejection(listDocumentChunks(WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, parent.signal));
    await vi.advanceTimersByTimeAsync(9_999);
    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await result).toMatchObject({ message: '请求超时，请稍后重试。' });
    expect(vi.getTimerCount()).toBe(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not accept a late successful response body after cancellation', async () => {
    let resolveBody!: (payload: unknown) => void;
    const response = jsonResponse(RESULT);
    vi.spyOn(response, 'json').mockReturnValueOnce(new Promise((resolve) => { resolveBody = resolve; }));
    fetchMock.mockResolvedValueOnce(response);
    const parent = new AbortController();
    const result = rejection(embed(5, parent.signal));
    await Promise.resolve();
    parent.abort();
    resolveBody(RESULT);
    expect(await result).toBeInstanceOf(EmbeddingUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
