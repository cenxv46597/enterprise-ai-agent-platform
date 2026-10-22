import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, getDocumentChunk } from './api';

const WORKSPACE = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const KNOWLEDGE_BASE = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const DOCUMENT = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const CHUNK = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
const OTHER = 'ffffffff-ffff-ffff-ffff-ffffffffffff';
const LIST_PATH = `/api/v1/workspaces/${WORKSPACE}/knowledge-bases/${KNOWLEDGE_BASE}/documents/${DOCUMENT}/chunks`;
const CHUNK_PATH = `${LIST_PATH}/${CHUNK}`;
const TRACE = 'f89ebaf3-bfe1-4aee-a379-c43f42a7bf8f';
const PRIVATE = 'PRIVATE_SINGLE_CHUNK_DETAIL';
const CONTENT = '完整块😀'.repeat(80), START = 450;
const LENGTH = Array.from(CONTENT).length;
const fetchMock = vi.fn<typeof fetch>();

function chunk(changes: Record<string, unknown> = {}) {
  return { id: CHUNK, document_id: DOCUMENT, chunk_index: 2, content: CONTENT,
    start_char: START, end_char: START + LENGTH, ...changes };
}
function json(payload: unknown, status = 200, trace?: string) {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (trace !== undefined) headers.set('X-Trace-Id', trace);
  return new Response(JSON.stringify(payload), { status, headers });
}
function read(signal = new AbortController().signal) {
  return getDocumentChunk(WORKSPACE, KNOWLEDGE_BASE, DOCUMENT, CHUNK, signal);
}
async function rejected(action: Promise<unknown>) {
  const failure: unknown = await action.then(() => { throw new Error('Expected single chunk rejection'); }, error => error);
  expect(failure).toBeInstanceOf(ApiError); expect(String(failure)).not.toContain(PRIVATE);
  return failure as ApiError;
}

beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => {
  const listReads = fetchMock.mock.calls.filter(([path, options]) => (options?.method ?? 'GET') === 'GET'
    && String(path).split('?')[0].endsWith('/chunks'));
  vi.restoreAllMocks(); vi.unstubAllGlobals();
  expect(listReads).toEqual([]);
});

describe('当前文档内按ID读取单个完整文本块', () => {
  it('第2块和非零起点合法；只GET规范目标URL并投影完整Unicode正文', async () => {
    fetchMock.mockResolvedValueOnce(json(chunk({ internal_detail: PRIVATE }), 200, TRACE));
    const result = await getDocumentChunk(WORKSPACE.toUpperCase(), KNOWLEDGE_BASE.toUpperCase(),
      DOCUMENT.toUpperCase(), CHUNK.toUpperCase(), new AbortController().signal);
    expect(result).toEqual({ id: CHUNK, documentId: DOCUMENT, chunkIndex: 2, content: CONTENT,
      startChar: START, endChar: START + LENGTH });
    expect(LENGTH).toBeGreaterThan(300); expect(CONTENT.length).toBeGreaterThan(LENGTH);
    expect(JSON.stringify(result)).not.toContain(PRIVATE); expect(JSON.stringify(result)).not.toContain(TRACE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe(CHUNK_PATH);
    expect(options).toMatchObject({ method: 'GET', credentials: 'same-origin', mode: 'same-origin',
      cache: 'no-store', redirect: 'error' });
    expect(options?.body).toBeUndefined();
    const headers = new Headers(options?.headers);
    expect(headers.get('X-EAAP-Web')).toBe('1');
    expect(headers.has('Authorization')).toBe(false); expect(headers.has('Cookie')).toBe(false);
    expect(headers.has('X-Trace-Id')).toBe(false);
  });

  it.each([
    { id: OTHER }, { document_id: OTHER }, { chunk_index: 0 }, { chunk_index: true },
    { start_char: -1 }, { end_char: Number.MAX_SAFE_INTEGER + 1 },
    { end_char: START + CONTENT.length }, { content: '' },
  ])('身份、父文档、序号、范围或码点长度无效时拒绝：%j', async changes => {
    fetchMock.mockResolvedValueOnce(json(chunk(changes), 200, TRACE));
    expect(await rejected(read())).toMatchObject({ status: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([null, [], [chunk()]].map(payload => ({ payload })))(
    '只接收单块对象，不把数组或null当单块：$payload', async ({ payload }) => {
      fetchMock.mockResolvedValueOnce(json(payload));
      await rejected(read()); expect(fetchMock).toHaveBeenCalledTimes(1);
    });

  it.each([401, 404, 503])('HTTP%s只保留本次失败头，不读错误正文、重试或回退整列表', async status => {
    const response = json({ detail: PRIVATE }, status, TRACE), body = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    expect(await rejected(read())).toMatchObject({ status, traceId: TRACE });
    expect(body).not.toHaveBeenCalled(); expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(CHUNK_PATH);
  });

  it('意外201不能展示正文，不解析该响应或补读列表', async () => {
    const response = json(chunk(), 201, TRACE), body = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    expect(await rejected(read())).toMatchObject({ status: null });
    expect(body).not.toHaveBeenCalled(); expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('坏JSON和网络异常均拒绝，不展示内部内容或追加读取', async () => {
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE)); await rejected(read());
    fetchMock.mockRejectedValueOnce(new Error(PRIVATE)); await rejected(read());
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.every(([path]) => path === CHUNK_PATH)).toBe(true);
  });

  it('非法范围或块ID以及预先取消均在发送GET前拒绝', async () => {
    const invalid: Array<[string, string, string, string]> = [
      ['bad', KNOWLEDGE_BASE, DOCUMENT, CHUNK], [WORKSPACE, '../outside', DOCUMENT, CHUNK],
      [WORKSPACE, KNOWLEDGE_BASE, `${DOCUMENT}\n`, CHUNK], [WORKSPACE, KNOWLEDGE_BASE, DOCUMENT, `${CHUNK}/../other`],
    ];
    for (const [workspace, knowledgeBase, document, chunkId] of invalid) {
      await rejected(getDocumentChunk(workspace, knowledgeBase, document, chunkId, new AbortController().signal));
    }
    const controller = new AbortController(); controller.abort(); await rejected(read(controller.signal));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
