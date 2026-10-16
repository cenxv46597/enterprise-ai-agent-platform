import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, listDocuments, type DocumentStatus } from './api';

const PRIVATE = 'PRIVATE-DOCUMENT-RESPONSE-DETAIL';
const WORKSPACE_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const KNOWLEDGE_BASE_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const DOCUMENT_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const OTHER_ID = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
const DOCUMENTS = `/api/v1/workspaces/${WORKSPACE_ID}/knowledge-bases/${KNOWLEDGE_BASE_ID}/documents?limit=20&offset=0`;
const DOCUMENT = {
  id: DOCUMENT_ID, knowledge_base_id: KNOWLEDGE_BASE_ID, original_filename: '研发资料.md', status: 'uploaded',
};
const INVALID_RESPONSE = '后端返回的数据无效，请稍后重试。';
const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
}

async function expectSafeFailure(action: Promise<unknown>, message: string, status: number | null): Promise<void> {
  const error: unknown = await action.then(() => {
    throw new Error('Expected API rejection');
  }, (failure: unknown) => failure);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({ message, status });
  expect(String(error)).not.toContain(PRIVATE);
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

describe('document first-page API boundary', () => {
  it('normalizes UUIDs, requests all five statuses, and retains only the public document fields', async () => {
    const statuses: DocumentStatus[] = ['uploaded', 'processing', 'parsed', 'ready', 'failed'];
    const page = statuses.map((status, index) => ({
      ...DOCUMENT,
      id: `CCCCCCCC-CCCC-CCCC-CCCC-${String(index).padStart(12, '0')}`,
      knowledge_base_id: KNOWLEDGE_BASE_ID.toUpperCase(),
      status,
      media_type: PRIVATE, byte_size: 100, sha256: PRIVATE, error_code: PRIVATE,
      created_at: PRIVATE, updated_at: PRIVATE,
    }));
    fetchMock.mockResolvedValueOnce(jsonResponse(page));
    expect(await listDocuments(
      WORKSPACE_ID.toUpperCase(), KNOWLEDGE_BASE_ID.toUpperCase(), new AbortController().signal,
    )).toEqual(page.map((item) => ({
      id: item.id.toLowerCase(), knowledgeBaseId: KNOWLEDGE_BASE_ID,
      originalFilename: item.original_filename, status: item.status,
    })));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe(DOCUMENTS);
    expect(String(path)).not.toContain('status=');
    expect(options).toMatchObject({
      method: 'GET', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
    });
    expect(options?.body).toBeUndefined();
    expect(options?.signal).toBeInstanceOf(AbortSignal);
    const headers = new Headers(options?.headers);
    expect(headers.get('X-EAAP-Web')).toBe('1');
    expect(headers.has('Authorization')).toBe(false);
    expect(headers.has('Cookie')).toBe(false);
  });

  it('accepts empty and full first pages and counts Unicode filename code points', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    expect(await listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal)).toEqual([]);
    const page = Array.from({ length: 20 }, (_, index) => ({
      ...DOCUMENT,
      id: `00000000-0000-0000-0000-${String(index).padStart(12, '0')}`,
      original_filename: index === 0 ? '😀'.repeat(255) : index === 1 ? '文' : `资料${index}.txt`,
    }));
    fetchMock.mockResolvedValueOnce(jsonResponse(page));
    expect(await listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal)).toEqual(
      page.map((item) => ({
        id: item.id, knowledgeBaseId: KNOWLEDGE_BASE_ID,
        originalFilename: item.original_filename, status: item.status,
      })),
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('rejects malformed structures, UUIDs, parent mismatches, duplicates and oversized pages as a whole', async () => {
    for (const payload of [
      { items: [DOCUMENT] }, null, [DOCUMENT, null], [DOCUMENT, []],
      [DOCUMENT, { ...DOCUMENT, id: PRIVATE }],
      [{ ...DOCUMENT, id: `${DOCUMENT_ID}\n` }],
      [{ ...DOCUMENT, id: undefined }],
      [{ ...DOCUMENT, knowledge_base_id: undefined }],
      [{ ...DOCUMENT, knowledge_base_id: PRIVATE }],
      [{ ...DOCUMENT, knowledge_base_id: `${KNOWLEDGE_BASE_ID}\n` }],
      [DOCUMENT, { ...DOCUMENT, id: OTHER_ID, knowledge_base_id: OTHER_ID }],
      [DOCUMENT, { ...DOCUMENT, id: DOCUMENT_ID.toUpperCase() }],
      Array.from({ length: 21 }, (_, index) => ({
        ...DOCUMENT, id: `00000000-0000-0000-0000-${String(index).padStart(12, '0')}`,
      })),
    ]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      await expectSafeFailure(
        listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal), INVALID_RESPONSE, null,
      );
    }
    expect(fetchMock).toHaveBeenCalledTimes(13);
  });

  it('rejects missing, non-string, empty or overlong filenames and unknown document states', async () => {
    const invalidRows = [
      { ...DOCUMENT, original_filename: undefined }, { ...DOCUMENT, original_filename: 123 },
      { ...DOCUMENT, original_filename: '' }, { ...DOCUMENT, original_filename: '😀'.repeat(256) },
      { ...DOCUMENT, status: undefined }, { ...DOCUMENT, status: null },
      { ...DOCUMENT, status: 'UPLOADED' }, { ...DOCUMENT, status: 'unknown' },
    ];
    for (const row of invalidRows) {
      fetchMock.mockResolvedValueOnce(jsonResponse([DOCUMENT, { ...row, id: OTHER_ID }]));
      await expectSafeFailure(
        listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal), INVALID_RESPONSE, null,
      );
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalidRows.length);
  });

  it('validates both selected parent IDs before making any request', async () => {
    for (const invalidId of ['', PRIVATE, ` ${KNOWLEDGE_BASE_ID}`, `${KNOWLEDGE_BASE_ID}\n`, `${KNOWLEDGE_BASE_ID}/other`]) {
      await expectSafeFailure(
        listDocuments(invalidId, KNOWLEDGE_BASE_ID, new AbortController().signal), INVALID_RESPONSE, null,
      );
      await expectSafeFailure(
        listDocuments(WORKSPACE_ID, invalidId, new AbortController().signal), INVALID_RESPONSE, null,
      );
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    [401, '登录已失效，请重新登录。'],
    [404, '请求未完成，请稍后重试。'],
  ] as const)('preserves a safe HTTP %s error without reading private response details', async (status, message) => {
    const response = jsonResponse({ detail: PRIVATE }, status);
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    await expectSafeFailure(
      listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal), message, status,
    );
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('contains malformed JSON and network errors without retrying', async () => {
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE));
    await expectSafeFailure(
      listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal), INVALID_RESPONSE, null,
    );
    fetchMock.mockRejectedValueOnce(new Error(PRIVATE));
    await expectSafeFailure(
      listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal),
      '无法连接后端，请检查服务后重试。', null,
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('cancels an in-flight request and rejects an already aborted signal without another request', async () => {
    fetchMock.mockImplementationOnce((_path, options) => new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
    }));
    const parent = new AbortController();
    const rejection = expectSafeFailure(
      listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, parent.signal), '请求已取消。', null,
    );
    parent.abort(PRIVATE);
    await rejection;
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    await expectSafeFailure(
      listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, parent.signal), '请求已取消。', null,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('uses the shared ten-second timeout and clears its timer', async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementationOnce((_path, options) => new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
    }));
    const rejection = expectSafeFailure(
      listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal), '请求超时，请稍后重试。', null,
    );
    await vi.advanceTimersByTimeAsync(10_000);
    await rejection;
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
