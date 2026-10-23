import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, DOCUMENT_FILENAME_QUERY_ERROR, DOCUMENT_PAGE_SIZE, MAX_DOCUMENT_OFFSET,
  isDocumentOffset, isDocumentStatusFilter, listDocuments, normalizeDocumentFilenameQuery,
  type DocumentStatus, type DocumentStatusFilter } from './api';

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

describe('document list API boundary', () => {
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

  it('sends the requested page and encodes a trimmed literal filename query without adding conditions', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([DOCUMENT]));
    const filenameQuery = '  研发 100%_\\+&?#.md  ';
    await listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal, 40, filenameQuery);
    const [path, options] = fetchMock.mock.calls[0];
    const url = new URL(String(path), 'http://localhost');
    expect(url.pathname).toBe(DOCUMENTS.split('?')[0]);
    expect([...url.searchParams.entries()]).toEqual([
      ['limit', '20'], ['offset', '40'], ['filename_query', filenameQuery.trim()],
    ]);
    expect(String(path)).toContain('%25_');
    expect(options).toMatchObject({ method: 'GET' });
    expect(options?.body).toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('omits a blank query, accepts 255 Unicode code points and the largest safe page offset', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal, 20, ' \t\n ');
    expect(fetchMock.mock.calls[0][0]).toBe(DOCUMENTS.replace('offset=0', 'offset=20'));
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal,
      MAX_DOCUMENT_OFFSET, '😀'.repeat(255));
    const url = new URL(String(fetchMock.mock.calls[1][0]), 'http://localhost');
    expect(url.searchParams.get('filename_query')).toBe('😀'.repeat(255));
    expect(url.searchParams.get('offset')).toBe(String(MAX_DOCUMENT_OFFSET));
    expect(DOCUMENT_PAGE_SIZE).toBe(20);
    expect(normalizeDocumentFilenameQuery('  报销 \n')).toBe('报销');
    expect(isDocumentOffset(MAX_DOCUMENT_OFFSET)).toBe(true);
    expect(isDocumentOffset(MAX_DOCUMENT_OFFSET + DOCUMENT_PAGE_SIZE)).toBe(false);
  });

  it.each<DocumentStatus>(['uploaded', 'processing', 'parsed', 'ready', 'failed'])(
    'requests the %s state together with the literal filename and requested page', async status => {
      fetchMock.mockResolvedValueOnce(jsonResponse([{ ...DOCUMENT, status, error_code: PRIVATE }]));
      expect(await listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal,
        20, '  报销%_/  ', status)).toEqual([{
        id: DOCUMENT_ID, knowledgeBaseId: KNOWLEDGE_BASE_ID, originalFilename: DOCUMENT.original_filename, status,
      }]);
      const [path, options] = fetchMock.mock.calls[0];
      expect([...new URL(String(path), 'http://localhost').searchParams.entries()]).toEqual([
        ['limit', '20'], ['offset', '20'], ['filename_query', '报销%_/'], ['status', status],
      ]);
      expect(options?.method).toBe('GET');
      expect(options?.body).toBeUndefined();
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(isDocumentStatusFilter(status)).toBe(true);
    },
  );

  it('omits all-state and blank filename conditions while supporting a status-only request', async () => {
    const mixedPage = [DOCUMENT, { ...DOCUMENT, id: OTHER_ID, status: 'failed' }];
    fetchMock.mockResolvedValueOnce(jsonResponse(mixedPage));
    const all = await listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal, 0, '', 'all');
    expect(all.map(row => row.status)).toEqual(['uploaded', 'failed']);
    expect(fetchMock.mock.calls[0][0]).toBe(DOCUMENTS);
    expect(isDocumentStatusFilter('all')).toBe(true);
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal, 0, ' \t ', 'failed');
    expect([...new URL(String(fetchMock.mock.calls[1][0]), 'http://localhost').searchParams.entries()]).toEqual([
      ['limit', '20'], ['offset', '0'], ['status', 'failed'],
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('rejects invalid state conditions before making any request without exposing the supplied value', async () => {
    for (const status of ['ALL', 'READY', ' ready', 'failed ', '', 'unknown', PRIVATE, '__proto__',
      null, true, 1, [], {}]) {
      expect(isDocumentStatusFilter(status)).toBe(false);
      await expectSafeFailure(listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal,
        0, '', status as DocumentStatusFilter),
      '文档状态条件无效，请重新选择。', null);
    }
    expect(isDocumentStatusFilter(undefined)).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each<DocumentStatus>(['uploaded', 'processing', 'parsed', 'ready', 'failed'])(
    'rejects an entire %s page when one returned row has another state', async status => {
      fetchMock.mockResolvedValueOnce(jsonResponse([
        { ...DOCUMENT, status },
        { ...DOCUMENT, id: OTHER_ID, status: status === 'ready' ? 'parsed' : 'ready', error_code: PRIVATE },
      ]));
      await expectSafeFailure(listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal,
        20, '报销', status), INVALID_RESPONSE, null);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it('rejects invalid page offsets before making any request', async () => {
    for (const offset of [-20, -1, 1, 19, 21, 20.5, NaN, Infinity, Number.MAX_SAFE_INTEGER,
      MAX_DOCUMENT_OFFSET + DOCUMENT_PAGE_SIZE, '20', null]) {
      expect(isDocumentOffset(offset)).toBe(false);
      await expectSafeFailure(
        listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal, offset as number),
        '文档列表页码无效，请返回首页后重试。', null,
      );
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects overlong, NUL-containing and non-string filename queries before making any request', async () => {
    for (const filenameQuery of ['文'.repeat(256), '😀'.repeat(256), '报\u0000销', '\u0000', 123, null]) {
      expect(() => normalizeDocumentFilenameQuery(filenameQuery)).toThrow(DOCUMENT_FILENAME_QUERY_ERROR);
      await expectSafeFailure(
        listDocuments(WORKSPACE_ID, KNOWLEDGE_BASE_ID, new AbortController().signal, 0, filenameQuery as string),
        DOCUMENT_FILENAME_QUERY_ERROR, null,
      );
    }
    expect(fetchMock).not.toHaveBeenCalled();
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
