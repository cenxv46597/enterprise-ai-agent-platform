import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError, CHUNK_UNCONFIRMED_MESSAGE, ChunkUnconfirmedError, createDocumentChunks, getDocument,
  listDocumentChunks,
} from './api';

const PRIVATE = 'PRIVATE-CHUNK-RESPONSE-DETAIL';
const WORKSPACE_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const KNOWLEDGE_BASE_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const DOCUMENT_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const CHUNK_ID = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
const OTHER_ID = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
const DOCUMENT_PATH = `/api/v1/workspaces/${WORKSPACE_ID}/knowledge-bases/${KNOWLEDGE_BASE_ID}/documents/${DOCUMENT_ID}`;
const INVALID_RESPONSE = '后端返回的数据无效，请稍后重试。';
const DOCUMENT = {
  id: DOCUMENT_ID, knowledge_base_id: KNOWLEDGE_BASE_ID, original_filename: '研发资料.md', status: 'parsed',
};
const CHUNK = { id: CHUNK_ID, document_id: DOCUMENT_ID, chunk_index: 1, content: '甲😀乙', start_char: 0, end_char: 3 };
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

const readDocument = (signal = new AbortController().signal) => getDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, signal);
const readChunks = (signal = new AbortController().signal) => listDocumentChunks(WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, signal);
const createChunks = (signal = new AbortController().signal) => createDocumentChunks(WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, signal);

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('document processing API boundary', () => {
  it('uses the scoped document GET and returns only matched public fields for all five states', async () => {
    for (const status of ['uploaded', 'processing', 'parsed', 'ready', 'failed']) {
      fetchMock.mockResolvedValueOnce(jsonResponse({
        ...DOCUMENT, id: DOCUMENT_ID.toUpperCase(), knowledge_base_id: KNOWLEDGE_BASE_ID.toUpperCase(),
        status, storage_key: PRIVATE, error_code: PRIVATE,
      }));
      expect(await getDocument(WORKSPACE_ID.toUpperCase(), KNOWLEDGE_BASE_ID.toUpperCase(), DOCUMENT_ID.toUpperCase(), new AbortController().signal))
        .toEqual({ id: DOCUMENT_ID, knowledgeBaseId: KNOWLEDGE_BASE_ID, originalFilename: DOCUMENT.original_filename, status });
    }
    expect(fetchMock).toHaveBeenCalledTimes(5);
    for (const [path, options] of fetchMock.mock.calls) {
      expect(path).toBe(DOCUMENT_PATH);
      expect(options).toMatchObject({ method: 'GET', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store' });
      expect(new Headers(options?.headers).get('X-EAAP-Web')).toBe('1');
      expect(options?.body).toBeUndefined();
    }
  });

  it('rejects document ID or knowledge-base mismatches, invalid names, states and non-200 success', async () => {
    const invalid = [
      null, [], { ...DOCUMENT, id: OTHER_ID }, { ...DOCUMENT, knowledge_base_id: OTHER_ID },
      { ...DOCUMENT, id: `${DOCUMENT_ID}\n` }, { ...DOCUMENT, original_filename: '' },
      { ...DOCUMENT, original_filename: '😀'.repeat(256) }, { ...DOCUMENT, status: 'done' },
    ];
    for (const payload of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      expect(await rejection(readDocument())).toMatchObject({ message: INVALID_RESPONSE, status: null });
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(DOCUMENT, 201));
    expect(await rejection(readDocument())).toMatchObject({ message: INVALID_RESPONSE, status: null });
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...DOCUMENT, original_filename: '😀'.repeat(255) }));
    expect((await readDocument()).originalFilename).toBe('😀'.repeat(255));
  });

  it('rejects all three invalid scope IDs before any request', async () => {
    const invalidId = `${DOCUMENT_ID}\n`;
    for (const operation of [getDocument, listDocumentChunks, createDocumentChunks]) {
      for (const args of [
        [invalidId, KNOWLEDGE_BASE_ID, DOCUMENT_ID], [WORKSPACE_ID, invalidId, DOCUMENT_ID],
        [WORKSPACE_ID, KNOWLEDGE_BASE_ID, invalidId],
      ]) {
        expect(await rejection(operation(args[0], args[1], args[2], new AbortController().signal)))
          .toMatchObject({ message: INVALID_RESPONSE, status: null });
      }
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('reads Unicode character ranges and existing non-default configurations, discarding raw fields', async () => {
    const rows = [
      { ...CHUNK, id: CHUNK_ID.toUpperCase(), document_id: DOCUMENT_ID.toUpperCase(), raw: PRIVATE },
      { ...CHUNK, id: OTHER_ID, chunk_index: 2, content: '乙丙', start_char: 2, end_char: 4 },
    ];
    fetchMock.mockResolvedValueOnce(jsonResponse(rows));
    expect(await readChunks()).toEqual([
      { id: CHUNK_ID, documentId: DOCUMENT_ID, chunkIndex: 1, content: '甲😀乙', startChar: 0, endChar: 3 },
      { id: OTHER_ID, documentId: DOCUMENT_ID, chunkIndex: 2, content: '乙丙', startChar: 2, endChar: 4 },
    ]);
    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe(`${DOCUMENT_PATH}/chunks`);
    expect(options?.method).toBe('GET');
    expect(options?.body).toBeUndefined();
    fetchMock.mockResolvedValueOnce(jsonResponse([
      { ...CHUNK, content: '甲', end_char: 1 },
      { ...CHUNK, id: OTHER_ID, chunk_index: 2, content: '乙', start_char: 1, end_char: 2 },
    ]));
    expect(await readChunks()).toHaveLength(2);
  });

  it('accepts no saved chunks on GET but treats an empty POST result as unconfirmed', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    expect(await readChunks()).toEqual([]);
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    const error = await rejection(createChunks());
    expect(error).toBeInstanceOf(ChunkUnconfirmedError);
    expect(error.message).toBe(CHUNK_UNCONFIRMED_MESSAGE);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('rejects the entire chunk response for malformed identity, sequence, content or ranges', async () => {
    const invalid: unknown[] = [
      null, { items: [CHUNK] }, [null], [{ ...CHUNK, id: `${CHUNK_ID}\n` }],
      [{ ...CHUNK, document_id: OTHER_ID }], [{ ...CHUNK, document_id: `${DOCUMENT_ID}\n` }],
      [{ ...CHUNK, chunk_index: 0 }], [{ ...CHUNK, chunk_index: '1' }],
      [{ ...CHUNK, content: '' }], [{ ...CHUNK, content: null }], [{ ...CHUNK, end_char: 4 }],
      [{ ...CHUNK, start_char: -1 }], [{ ...CHUNK, start_char: 1, end_char: 4 }],
      [{ ...CHUNK, end_char: 0 }], [{ ...CHUNK, end_char: 3.5 }],
      [{ ...CHUNK, end_char: Number.MAX_SAFE_INTEGER + 1 }],
      [CHUNK, { ...CHUNK, id: CHUNK_ID.toUpperCase(), chunk_index: 2, start_char: 2, end_char: 5 }],
      [CHUNK, { ...CHUNK, id: OTHER_ID, chunk_index: 3, start_char: 2, end_char: 5 }],
      [CHUNK, { ...CHUNK, id: OTHER_ID, chunk_index: 2, start_char: 4, end_char: 7 }],
      [CHUNK, { ...CHUNK, id: OTHER_ID, chunk_index: 2 }],
      [CHUNK, { ...CHUNK, id: OTHER_ID, chunk_index: 2, content: '乙', start_char: 2, end_char: 3 }],
      Array.from({ length: 10_001 }, () => CHUNK),
    ];
    for (const payload of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      expect(await rejection(readChunks())).toMatchObject({ message: INVALID_RESPONSE, status: null });
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('enforces the shared ten-Mi-character payload cap even when individual ranges are valid', async () => {
    const content = '文'.repeat(6 * 1024 * 1024);
    fetchMock.mockResolvedValueOnce(jsonResponse([
      { ...CHUNK, content, end_char: content.length },
      { ...CHUNK, id: OTHER_ID, chunk_index: 2, content, start_char: 1, end_char: content.length + 1 },
    ]));
    expect(await rejection(readChunks())).toMatchObject({ message: INVALID_RESPONSE, status: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('posts the fixed explicit parameters using the same-origin cookie boundary without retry', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([CHUNK]));
    expect(await createChunks()).toEqual([
      { id: CHUNK_ID, documentId: DOCUMENT_ID, chunkIndex: 1, content: '甲😀乙', startChar: 0, endChar: 3 },
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe(`${DOCUMENT_PATH}/chunks`);
    expect(options).toMatchObject({
      method: 'POST', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
      body: JSON.stringify({ chunk_size: 500, overlap: 50 }),
    });
    expect(options?.signal).toBeInstanceOf(AbortSignal);
    const headers = new Headers(options?.headers);
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('X-EAAP-Web')).toBe('1');
    expect(headers.has('Authorization')).toBe(false);
    expect(headers.has('Cookie')).toBe(false);
  });

  it.each([
    [401, '登录已失效，请重新登录。'],
    [403, '切块请求被拒绝，请检查登录与浏览器来源后重试。'],
    [404, '该文档不存在或你已无权访问，请返回文档列表。'],
    [409, '当前文档状态或已有切块配置不允许执行本次切块，请刷新文档与切块记录。'],
    [422, '切块请求未被接受，参数或单次处理量可能超过后端限制。'],
  ] as const)('keeps HTTP %s as a safe explicit rejection without exposing or guessing backend detail', async (status, message) => {
    const response = jsonResponse({ detail: PRIVATE }, status);
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    const error = await rejection(createChunks());
    expect(error).not.toBeInstanceOf(ChunkUnconfirmedError);
    expect(error).toMatchObject({ status, message });
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('treats unexpected HTTP results, invalid JSON, wrong ownership and network failures as unconfirmed', async () => {
    for (const status of [201, 202, 400, 429, 500, 503]) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, status));
      const error = await rejection(createChunks());
      expect(error).toBeInstanceOf(ChunkUnconfirmedError);
      expect(error).toMatchObject({ message: CHUNK_UNCONFIRMED_MESSAGE, status });
    }
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE));
    expect(await rejection(createChunks())).toBeInstanceOf(ChunkUnconfirmedError);
    fetchMock.mockResolvedValueOnce(jsonResponse([{ ...CHUNK, document_id: OTHER_ID }]));
    expect(await rejection(createChunks())).toBeInstanceOf(ChunkUnconfirmedError);
    fetchMock.mockRejectedValueOnce(new Error(PRIVATE));
    expect(await rejection(createChunks())).toBeInstanceOf(ChunkUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(9);
  });

  it('does not send any processing request with an already aborted parent signal', async () => {
    const parent = new AbortController();
    parent.abort();
    for (const action of [readDocument, readChunks, createChunks]) {
      const error = await rejection(action(parent.signal));
      expect(error).not.toBeInstanceOf(ChunkUnconfirmedError);
      expect(error.message).toBe('请求已取消。');
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['cancel', 'timeout'] as const)('keeps a sent mutation unconfirmed on %s and never automatically repeats it', async (interruption) => {
    vi.useFakeTimers();
    fetchMock.mockImplementationOnce((_path, options) => new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
    }));
    const parent = new AbortController();
    const result = rejection(createChunks(parent.signal));
    if (interruption === 'cancel') parent.abort();
    else await vi.advanceTimersByTimeAsync(10_000);
    const error = await result;
    expect(error).toBeInstanceOf(ChunkUnconfirmedError);
    expect(error.message).toBe(CHUNK_UNCONFIRMED_MESSAGE);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('does not accept a late successful body after cancellation during JSON reading', async () => {
    let resolveBody!: (payload: unknown) => void;
    const response = jsonResponse([CHUNK]);
    vi.spyOn(response, 'json').mockReturnValueOnce(new Promise((resolve) => { resolveBody = resolve; }));
    fetchMock.mockResolvedValueOnce(response);
    const parent = new AbortController();
    const result = rejection(createChunks(parent.signal));
    await Promise.resolve();
    parent.abort();
    resolveBody([CHUNK]);
    expect(await result).toBeInstanceOf(ChunkUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('keeps document and chunk GET errors safe without retrying or treating them as mutations', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, 401));
    expect(await rejection(readDocument())).toMatchObject({ status: 401, message: '登录已失效，请重新登录。' });
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, 404));
    expect(await rejection(readChunks())).toMatchObject({ status: 404, message: '请求未完成，请稍后重试。' });
    fetchMock.mockResolvedValueOnce(jsonResponse([CHUNK], 201));
    expect(await rejection(readChunks())).toMatchObject({ status: null, message: INVALID_RESPONSE });
    fetchMock.mockRejectedValueOnce(new Error(PRIVATE));
    const error = await rejection(readChunks());
    expect(error).not.toBeInstanceOf(ChunkUnconfirmedError);
    expect(error.message).toBe('无法连接后端，请检查服务后重试。');
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});
