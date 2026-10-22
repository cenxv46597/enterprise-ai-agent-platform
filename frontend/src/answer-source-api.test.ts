import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from './api';
import type { AnswerCitation } from './api';
import { AnswerCitationSourceError, readAnswerCitationSource } from './answer-source-api';

const WORKSPACE = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const KNOWLEDGE_BASE = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const DOCUMENT = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const CHUNK = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
const OTHER = 'ffffffff-ffff-ffff-ffff-ffffffffffff';
const DOCUMENT_PATH = `/api/v1/workspaces/${WORKSPACE}/knowledge-bases/${KNOWLEDGE_BASE}/documents/${DOCUMENT}`;
const CHUNK_PATH = `${DOCUMENT_PATH}/chunks/${CHUNK}`;
const TRACE_A = 'f89ebaf3-bfe1-4aee-a379-c43f42a7bf8f';
const TRACE_B = '4ea9ee41-af23-4c44-a103-2231d4e03b43';
const PRIVATE = 'PRIVATE-SOURCE-DETAIL';
const MESSAGE = '无法确认这条引文的当前来源段落，请稍后手动重新读取。';
const CONTENT = '😀前句。引文😀，中间文字，重复。重复。' + '完整正文😀'.repeat(70);
const QUOTE = '引文😀';
const CHUNK_START = 2;
const QUOTE_START = CHUNK_START + Array.from(CONTENT).indexOf('引');
const CITATION: AnswerCitation = {
  chunkId: CHUNK, documentId: DOCUMENT, documentName: '来源😀.md', chunkIndex: 2,
  quote: QUOTE, startChar: QUOTE_START, endChar: QUOTE_START + Array.from(QUOTE).length,
};
const fetchMock = vi.fn<typeof fetch>();

function documentResponse(changes: Record<string, unknown> = {}) {
  return { id: DOCUMENT, knowledge_base_id: KNOWLEDGE_BASE,
    original_filename: CITATION.documentName, status: 'ready', ...changes };
}

function chunkResponse(changes: Record<string, unknown> = {}) {
  return { id: CHUNK, document_id: DOCUMENT, chunk_index: 2, content: CONTENT,
    start_char: CHUNK_START, end_char: CHUNK_START + Array.from(CONTENT).length, ...changes };
}

function json(payload: unknown, status = 200, traceId?: string): Response {
  return new Response(JSON.stringify(payload), { status, headers: {
    'Content-Type': 'application/json', ...(traceId ? { 'X-Trace-Id': traceId } : {}),
  } });
}

function read(citation = CITATION, signal = new AbortController().signal) {
  return readAnswerCitationSource(WORKSPACE, KNOWLEDGE_BASE, citation, signal);
}

async function rejected(action: Promise<unknown>): Promise<AnswerCitationSourceError> {
  const failure: unknown = await action.then(() => { throw new Error('Expected source rejection'); }, error => error);
  expect(failure).toBeInstanceOf(AnswerCitationSourceError);
  expect(failure).toMatchObject({ name: 'AnswerCitationSourceError', message: MESSAGE });
  expect(String(failure)).not.toContain(PRIVATE);
  return failure as AnswerCitationSourceError;
}

beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => {
  const listReads = fetchMock.mock.calls.filter(([path, options]) => (options?.method ?? 'GET') === 'GET'
    && String(path).split('?')[0].endsWith('/chunks'));
  vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  expect(listReads).toEqual([]);
});

describe('手动读取单条引文的当前完整来源块', () => {
  it('读取当前范围文档与指定单块，按Unicode位置核对，只投影目标块且不截断正文', async () => {
    fetchMock.mockResolvedValueOnce(json(documentResponse(), 200, TRACE_A))
      .mockResolvedValueOnce(json(chunkResponse({ raw: PRIVATE }), 200, TRACE_B));
    const result = await readAnswerCitationSource(WORKSPACE.toUpperCase(), KNOWLEDGE_BASE.toUpperCase(), {
      ...CITATION, chunkId: CHUNK.toUpperCase(), documentId: DOCUMENT.toUpperCase(),
    }, new AbortController().signal);
    expect(result).toEqual({ id: CHUNK, documentId: DOCUMENT, chunkIndex: 2, content: CONTENT,
      startChar: CHUNK_START, endChar: CHUNK_START + Array.from(CONTENT).length });
    expect(Array.from(result.content).length).toBeGreaterThan(300);
    expect(JSON.stringify(result)).not.toContain(PRIVATE);
    expect(JSON.stringify(result)).not.toContain(TRACE_A);
    expect(JSON.stringify(result)).not.toContain(TRACE_B);
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([DOCUMENT_PATH, CHUNK_PATH]);
    for (const [, options] of fetchMock.mock.calls) {
      expect(options).toMatchObject({ method: 'GET', credentials: 'same-origin', mode: 'same-origin',
        cache: 'no-store', redirect: 'error' });
      expect(options?.body).toBeUndefined();
      expect(new Headers(options?.headers).get('X-EAAP-Web')).toBe('1');
      expect(new Headers(options?.headers).has('Authorization')).toBe(false);
    }
  });

  it('复制输入目标，等待中修改原对象不能把读取改绑到另一块或另一文档', async () => {
    let finish!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
      .mockResolvedValueOnce(json(chunkResponse()));
    const target = { ...CITATION }, action = read(target);
    target.documentId = OTHER; target.chunkId = OTHER; target.quote = PRIVATE;
    target.startChar = 0; target.endChar = 1;
    finish(json(documentResponse()));
    expect((await action).id).toBe(CHUNK);
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([DOCUMENT_PATH, CHUNK_PATH]);
  });

  it('重复文本必须出现在指定范围；其它位置存在相同字样不能放行', async () => {
    const first = Array.from(CONTENT).join('').indexOf('重复');
    const codePointFirst = Array.from(CONTENT.slice(0, first)).length;
    const correct = { ...CITATION, quote: '重复', startChar: CHUNK_START + codePointFirst,
      endChar: CHUNK_START + codePointFirst + 2 };
    fetchMock.mockResolvedValueOnce(json(documentResponse())).mockResolvedValueOnce(json(chunkResponse()));
    expect((await read(correct)).content).toBe(CONTENT);
    fetchMock.mockResolvedValueOnce(json(documentResponse())).mockResolvedValueOnce(json(chunkResponse()));
    expect(await rejected(read({ ...correct, startChar: correct.startChar + 1, endChar: correct.endChar + 1 })))
      .toMatchObject({ status: null, traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it.each([
    { chunkId: 'bad' }, { documentId: `${DOCUMENT}\n` }, { documentName: '' },
    { chunkIndex: 0 }, { chunkIndex: true }, { quote: ' \n' }, { quote: '😀'.repeat(1001) },
    { startChar: -1 }, { startChar: 1.5 }, { endChar: Number.MAX_SAFE_INTEGER + 1 },
    { endChar: CITATION.endChar + 1 },
  ])('输入目标无效时不读取来源：%j', async changes => {
    expect(await rejected(read({ ...CITATION, ...changes } as AnswerCitation)))
      .toMatchObject({ status: null, traceId: null });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('范围UUID无效或预先取消均不发送GET', async () => {
    expect(await rejected(readAnswerCitationSource('bad', KNOWLEDGE_BASE, CITATION, new AbortController().signal)))
      .toMatchObject({ status: null, traceId: null });
    const controller = new AbortController(); controller.abort();
    expect(await rejected(read(CITATION, controller.signal))).toMatchObject({ status: null, traceId: null });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    { id: OTHER }, { knowledge_base_id: OTHER }, { original_filename: '另一份.md' },
    { status: 'parsed' }, { status: 'failed' },
  ])('来源文档归属或状态不匹配时不读块，成功编号不进入校验错误：%j', async changes => {
    fetchMock.mockResolvedValueOnce(json(documentResponse(changes), 200, TRACE_A));
    expect(await rejected(read())).toMatchObject({ status: null, traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    { id: OTHER }, { document_id: OTHER }, { chunk_index: 3 }, { content: '甲'.repeat(Array.from(CONTENT).length) },
    { start_char: CITATION.startChar + 1, end_char: CITATION.startChar + 1 + Array.from(CONTENT).length },
    { end_char: CHUNK_START + Array.from(CONTENT).length + 1 },
  ])('块身份、归属、序号、范围或逐字引文不匹配时拒绝：%j', async changes => {
    fetchMock.mockResolvedValueOnce(json(documentResponse(), 200, TRACE_A))
      .mockResolvedValueOnce(json(chunkResponse(changes), 200, TRACE_B));
    expect(await rejected(read())).toMatchObject({ status: null, traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('数组冒充单块响应和超出块边界的引文均不返回任何正文', async () => {
    fetchMock.mockResolvedValueOnce(json(documentResponse())).mockResolvedValueOnce(json([]));
    expect(await rejected(read())).toMatchObject({ traceId: null });
    fetchMock.mockResolvedValueOnce(json(documentResponse())).mockResolvedValueOnce(json(chunkResponse()));
    expect(await rejected(read({ ...CITATION, quote: '前导', startChar: 0, endChar: 2 })))
      .toMatchObject({ traceId: null });
  });

  it.each(['document', 'chunks'] as const)('HTTP失败仅保留实际%s失败编号，不解析私有错误正文或重试', async stage => {
    if (stage === 'chunks') fetchMock.mockResolvedValueOnce(json(documentResponse(), 200, TRACE_A));
    const response = json({ detail: PRIVATE }, 503, TRACE_B), body = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    expect(await rejected(read())).toMatchObject({ status: 503, traceId: TRACE_B });
    expect(body).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(stage === 'document' ? 1 : 2);
  });

  it.each([undefined, 'not-a-trace-id'])('末HTTP失败没有合法编号时不回退使用文档成功编号：%s', async trace => {
    fetchMock.mockResolvedValueOnce(json(documentResponse(), 200, TRACE_A))
      .mockResolvedValueOnce(json({ detail: PRIVATE }, 404, trace));
    expect(await rejected(read())).toMatchObject({ status: 404, traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(['document', 'chunks'] as const)('未取消的%s来源401原样抛出供App清理会话', async stage => {
    const unauthorized = new api.ApiError('登录已失效，请重新登录。', 401, TRACE_B);
    if (stage === 'document') vi.spyOn(api, 'getDocument').mockRejectedValueOnce(unauthorized);
    else {
      fetchMock.mockResolvedValueOnce(json(documentResponse(), 200, TRACE_A));
      vi.spyOn(api, 'getDocumentChunk').mockRejectedValueOnce(unauthorized);
    }
    await expect(read()).rejects.toBe(unauthorized);
    expect(fetchMock).toHaveBeenCalledTimes(stage === 'document' ? 0 : 1);
  });

  it('意外成功状态、坏JSON和网络失败都不使用先前或成功响应编号', async () => {
    for (const response of [json(chunkResponse(), 201, TRACE_B), new Response(PRIVATE, { headers: { 'X-Trace-Id': TRACE_B } })]) {
      fetchMock.mockResolvedValueOnce(json(documentResponse(), 200, TRACE_A)).mockResolvedValueOnce(response);
      expect(await rejected(read())).toMatchObject({ status: null, traceId: null });
    }
    fetchMock.mockResolvedValueOnce(json(documentResponse(), 200, TRACE_A)).mockRejectedValueOnce(new Error(PRIVATE));
    expect(await rejected(read())).toMatchObject({ status: null, traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });

  it.each(['document', 'chunks'] as const)('%s等待中取消，迟到401不能恢复编号、清会话或追加读取', async stage => {
    if (stage === 'chunks') fetchMock.mockResolvedValueOnce(json(documentResponse(), 200, TRACE_A));
    let finish!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const controller = new AbortController(), action = rejected(read(CITATION, controller.signal));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(stage === 'document' ? 1 : 2));
    controller.abort();
    finish(json({ detail: PRIVATE }, 401, TRACE_B));
    expect(await action).toMatchObject({ status: null, traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(stage === 'document' ? 1 : 2);
  });

  it('块JSON解析期间取消，迟到合法正文不能返回或携带成功编号', async () => {
    let finish!: (payload: unknown) => void;
    const response = json(chunkResponse(), 200, TRACE_B);
    const body = vi.spyOn(response, 'json').mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    fetchMock.mockResolvedValueOnce(json(documentResponse(), 200, TRACE_A)).mockResolvedValueOnce(response);
    const controller = new AbortController(), action = rejected(read(CITATION, controller.signal));
    await vi.waitFor(() => expect(body).toHaveBeenCalled());
    controller.abort(); finish(chunkResponse());
    expect(await action).toMatchObject({ status: null, traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('块GET独立十秒超时后，迟到正文不返回并清理定时器', async () => {
    vi.useFakeTimers();
    let finish!: (response: Response) => void;
    fetchMock.mockResolvedValueOnce(json(documentResponse(), 200, TRACE_A))
      .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const action = rejected(read());
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(true);
    finish(json(chunkResponse(), 200, TRACE_B));
    expect(await action).toMatchObject({ status: null, traceId: null });
    expect(vi.getTimerCount()).toBe(0); expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
