import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';

const USER = '00000000-0000-0000-0000-000000000001';
const SPACE = '00000000-0000-0000-0000-00000000000a';
const KB = '00000000-0000-0000-0000-000000000101';
const DOCUMENT_A = '00000000-0000-0000-0000-000000000301';
const DOCUMENT_B = '00000000-0000-0000-0000-000000000302';
const CHUNK_A = '00000000-0000-0000-0000-000000000501';
const CHUNK_B = '00000000-0000-0000-0000-000000000502';
const RESTORE = '/api/v1/auth/web/session', LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces', KNOWLEDGE = `${SPACES}/${SPACE}/knowledge-bases`;
const DOCUMENTS = `${KNOWLEDGE}/${KB}/documents`, ANSWERS = `${KNOWLEDGE}/${KB}/answers`;
const DETAIL_A = `${DOCUMENTS}/${DOCUMENT_A}`, DETAIL_B = `${DOCUMENTS}/${DOCUMENT_B}`;
const CHUNK_A_PATH = `${DETAIL_A}/chunks/${CHUNK_A}`, CHUNK_B_PATH = `${DETAIL_B}/chunks/${CHUNK_B}`;
const FILENAME_A = '导出说明.md', FILENAME_B = '保留期限.md';
const QUESTION = '导出资料如何组织？', NEXT_QUESTION = '下一次问题是什么？';
const ANSWER = '导出资料保留名称和日期等信息，保留期限见来源。';
const ABSTENTION = '当前资料不足以回答下一次问题。';
const PRIVATE = 'PRIVATE_SOURCE_RESPONSE_MUST_NOT_BE_DISPLAYED';
const TRACE_A = 'a0e0b901-2b0d-4ae0-812a-bab2cf9e7b01';
const TRACE_B = 'b1f1c012-3c1e-4bf1-923b-cbc3dfa08c12';
const PREFIX_A = '仅原文中的前置说明🙂'.repeat(40);
const QUOTE_A = '支持导出🙂<img src=x onerror="alert(1)">后的资料。';
const QUOTE_SECOND = '同一来源段落还保留创建时间。';
const BETWEEN_A = '随后还有额外背景说明。'.repeat(20);
const SUFFIX_A = '完整来源的结尾不能截断。'.repeat(30);
const CONTENT_A = `${PREFIX_A}${QUOTE_A}${BETWEEN_A}${QUOTE_SECOND}${SUFFIX_A}`;
const PREFIX_B = '另一份资料的前置上下文。', QUOTE_B = '导出资料保留七天。';
const CONTENT_B = `${PREFIX_B}${QUOTE_B}另一份完整来源的末尾。`;

type Route = (request: Request) => Response | Promise<Response> | undefined;
const characters = (value: string) => Array.from(value).length;

function json(body: unknown, status = 200, trace?: string) {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (trace !== undefined) headers.set('X-Trace-Id', trace);
  return new Response(JSON.stringify(body), { status, headers });
}
function sessionResponse(expiresAt = Date.now() / 1000 + 1800) {
  return json({ user: { id: USER, username: 'demo_a' }, expires_at: expiresAt });
}
function documentRow(id = DOCUMENT_A) {
  return { id, knowledge_base_id: KB, original_filename: id === DOCUMENT_A ? FILENAME_A : FILENAME_B,
    status: 'ready', internal_detail: PRIVATE };
}
function citation(index: number) {
  const quote = [QUOTE_A, QUOTE_SECOND, QUOTE_B][index];
  const start = [characters(PREFIX_A), characters(PREFIX_A + QUOTE_A + BETWEEN_A), characters(PREFIX_B)][index];
  return { chunk_id: index === 2 ? CHUNK_B : CHUNK_A, document_id: index === 2 ? DOCUMENT_B : DOCUMENT_A,
    document_name: index === 2 ? FILENAME_B : FILENAME_A, chunk_index: 1,
    quote, start_char: start, end_char: start + characters(quote) };
}
function answerResult() {
  return { answer: ANSWER, abstained: false, citations: [0, 1, 2].map(citation), reranking: null };
}
function chunkResponse(documentId = DOCUMENT_A, content = documentId === DOCUMENT_A ? CONTENT_A : CONTENT_B) {
  return { id: documentId === DOCUMENT_A ? CHUNK_A : CHUNK_B, document_id: documentId,
    chunk_index: 1, content, start_char: 0, end_char: characters(content) };
}
function deferred() {
  let resolve!: (response: Response) => void;
  return { promise: new Promise<Response>(done => { resolve = done; }), resolve: (response: Response) => resolve(response) };
}
function pathOf(request: Request) { return new URL(request.url).pathname; }
function answerPosts(requests: Request[]) { return requests.filter(request => request.method === 'POST' && pathOf(request) === ANSWERS); }
function chunkReads(requests: Request[]) { return requests.filter(request => [CHUNK_A_PATH, CHUNK_B_PATH].includes(pathOf(request))); }

// All traffic is a fixture; aborted requests may deliberately resolve late.
function installApi(route: Route = () => undefined) {
  const requests: Request[] = [];
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const request = new Request(new URL(String(input), 'http://localhost'), init); requests.push(request);
    const response = route(request); if (response !== undefined) return response;
    switch (pathOf(request)) {
      case RESTORE: return sessionResponse();
      case LOGOUT: return new Response(null, { status: 204 });
      case SPACES: return json([{ id: SPACE, name: '甲空间' }]);
      case KNOWLEDGE: return json([{ id: KB, workspace_id: SPACE, name: '甲知识库' }]);
      case DOCUMENTS: return json([documentRow()]);
      case DETAIL_A: return json(documentRow());
      case DETAIL_B: return json(documentRow(DOCUMENT_B));
      case CHUNK_A_PATH: return json(chunkResponse());
      case CHUNK_B_PATH: return json(chunkResponse(DOCUMENT_B));
      case `${DETAIL_A}/chunks`:
      case `${DETAIL_B}/chunks`: throw new Error('Source previews must not read the whole chunk list');
      case ANSWERS: return json(answerResult());
      default: throw new Error('Unexpected answer-source fixture endpoint');
    }
  }));
  return requests;
}
async function advance(milliseconds = 0) { await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); }); }
async function click(element: HTMLElement) { await act(async () => { fireEvent.click(element); await vi.advanceTimersByTimeAsync(0); }); }
async function resolve(pending: ReturnType<typeof deferred>, response: Response) {
  await act(async () => { pending.resolve(response); await vi.advanceTimersByTimeAsync(0); });
}
async function submit(question = QUESTION) {
  await act(async () => { fireEvent.change(screen.getByLabelText('问答问题'), { target: { value: question } }); });
  await click(screen.getByRole('button', { name: '生成回答' }));
}
async function prepare() {
  render(<App />); await advance(); await click(screen.getByRole('button', { name: '进入甲空间' }));
  await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
  await click(screen.getByRole('button', { name: '引用问答' })); await submit();
}
function sourceButton(index = 1) {
  return screen.getByRole('button', { name: `查看引用 ${index} 的完整来源段落` }) as HTMLButtonElement;
}
function sourceRegion(index = 1) { return screen.getByRole('region', { name: `引用 ${index} 的完整来源段落` }); }
function answerRegion() { return screen.getByRole('region', { name: '本次问答结果' }); }
function expectConfirmedAnswer() {
  expect(within(answerRegion()).getByText(ANSWER)).toBeTruthy();
  expect(within(answerRegion()).getByText(QUESTION)).toBeTruthy();
  expect(screen.queryByText(/问答结果未确认/)).toBeNull();
}
function expectNoFullSource() {
  for (const value of [PREFIX_A, SUFFIX_A, PREFIX_B]) expect(document.body.textContent).not.toContain(value);
}
function expectNoTrace() {
  for (const value of [TRACE_A, TRACE_B, '排错编号：']) expect(document.body.textContent).not.toContain(value);
}
function expectOnlyAnswerWrites(requests: Request[], count = 1) {
  expect(answerPosts(requests)).toHaveLength(count);
  expect(requests.filter(request => request.method !== 'GET' && pathOf(request) !== LOGOUT)
    .map(request => [request.method, pathOf(request)])).toEqual(Array.from({ length: count }, () => ['POST', ANSWERS]));
  expect(requests.filter(request => request.method === 'GET'
    && pathOf(request).endsWith('/chunks'))).toHaveLength(0);
  expect(document.body.textContent).not.toContain(PRIVATE);
}

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2030-01-01T00:00:00Z')); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('引用问答中手动查看完整来源段落', () => {
  it('答案出现不自动读块；点击才按范围GET详情和块，完整Unicode正文与当前引文安全高亮', async () => {
    const requests = installApi(); await prepare(); expectConfirmedAnswer();
    expect(chunkReads(requests)).toHaveLength(0); expectNoFullSource();
    const before = requests.length; await advance(30_000); expect(requests).toHaveLength(before);
    await click(sourceButton());
    const preview = sourceRegion();
    expect(preview.textContent).toContain(CONTENT_A); expect(characters(CONTENT_A)).toBeGreaterThan(300);
    expect(preview.querySelectorAll('mark')).toHaveLength(1);
    expect(preview.querySelector('mark')?.textContent).toBe(QUOTE_A);
    expect(document.querySelector('img')).toBeNull(); expect(document.querySelector('script')).toBeNull();
    expect(requests.slice(before).map(pathOf)).toEqual([DETAIL_A, CHUNK_A_PATH]);
    for (const request of requests.slice(before)) {
      expect(request.method).toBe('GET'); expect(request.body).toBeNull();
      expect(request.credentials).toBe('same-origin'); expect(request.headers.get('X-EAAP-Web')).toBe('1');
      expect(request.headers.has('Authorization')).toBe(false); expect(request.headers.has('X-Trace-Id')).toBe(false);
    }
    expectConfirmedAnswer(); expectOnlyAnswerWrites(requests);
  });

  it('来源等待中保留原答案，禁用新提交和其他来源，双击不产生第二次读取', async () => {
    const pending = deferred(); const requests = installApi(request => pathOf(request) === CHUNK_A_PATH ? pending.promise : undefined);
    await prepare(); const before = requests.length; await click(sourceButton());
    expectConfirmedAnswer(); expectNoFullSource();
    expect((screen.getByRole('button', { name: '生成回答' }) as HTMLButtonElement).disabled).toBe(true);
    expect(sourceButton(2).disabled).toBe(true); expect(sourceButton(3).disabled).toBe(true);
    await click(sourceButton()); await click(sourceButton(3));
    await click(screen.getByRole('button', { name: '生成回答' }));
    expect(requests.slice(before).map(pathOf)).toEqual([DETAIL_A, CHUNK_A_PATH]);
    await resolve(pending, json(chunkResponse()));
    expect(sourceRegion().textContent).toContain(CONTENT_A);
    expect(sourceButton(3).disabled).toBe(false); expectOnlyAnswerWrites(requests);
  });

  it('同块另一条引文使用自己的位置高亮；只有一个来源区，关闭不请求或清除答案', async () => {
    const requests = installApi(); await prepare(); await click(sourceButton()); await click(sourceButton(2));
    expect(screen.queryByRole('region', { name: '引用 1 的完整来源段落' })).toBeNull();
    expect(sourceRegion(2).textContent).toContain(CONTENT_A);
    expect(sourceRegion(2).querySelectorAll('mark')).toHaveLength(1);
    expect(sourceRegion(2).querySelector('mark')?.textContent).toBe(QUOTE_SECOND);
    const before = requests.length; await click(screen.getByRole('button', { name: '关闭来源段落' }));
    expect(requests).toHaveLength(before); expectNoFullSource(); expectConfirmedAnswer(); expectOnlyAnswerWrites(requests);
  });

  it('等待时关闭来源会取消请求；同一答案重新打开成功后，旧失败不能覆盖新预览或带回编号', async () => {
    const old = deferred(); let reads = 0;
    const requests = installApi(request => pathOf(request) === CHUNK_A_PATH && ++reads === 1 ? old.promise : undefined);
    await prepare(); await click(sourceButton());
    const cancelled = chunkReads(requests)[0];
    await click(screen.getByRole('button', { name: '关闭来源段落' }));
    expect(cancelled.signal.aborted).toBe(true); expectConfirmedAnswer(); expectNoFullSource();
    expect(screen.queryByRole('region', { name: '引用 1 的完整来源段落' })).toBeNull();
    await click(sourceButton());
    expect(sourceRegion().textContent).toContain(CONTENT_A);
    expect(sourceRegion().querySelector('mark')?.textContent).toBe(QUOTE_A);
    expect(chunkReads(requests)).toHaveLength(2);
    const before = requests.length; await resolve(old, json({ detail: PRIVATE }, 503, TRACE_B));
    expect(requests).toHaveLength(before); expectConfirmedAnswer(); expectNoTrace();
    expect(sourceRegion().textContent).toContain(CONTENT_A);
    expect(sourceRegion().querySelector('mark')?.textContent).toBe(QUOTE_A);
    expectOnlyAnswerWrites(requests);
  });

  it.each(['detail', 'chunks'] as const)('另一来源的%s GET失败仅展示该失败编号，清旧全文但保留已确认答案', async stage => {
    let documentReads = 0;
    const requests = installApi(request => {
      if (pathOf(request) === DETAIL_B && ++documentReads > 1 && stage === 'detail') return json({ detail: PRIVATE }, 503, TRACE_A);
      if (pathOf(request) === CHUNK_B_PATH && stage === 'chunks') return json({ detail: PRIVATE }, 503, TRACE_A);
    });
    await prepare(); await click(sourceButton()); expect(sourceRegion().textContent).toContain(CONTENT_A);
    await click(sourceButton(3)); expectNoFullSource(); expectConfirmedAnswer();
    expect(within(sourceRegion(3)).getByText(TRACE_A).tagName).toBe('CODE');
    expect(screen.queryByRole('region', { name: '引用 1 的完整来源段落' })).toBeNull();
    const before = requests.length; await advance(30_000); expect(requests).toHaveLength(before);
    expectOnlyAnswerWrites(requests);
  });

  it('200返回的当前块引文已变化时不展示完整正文，也不把成功HTTP编号当失败编号', async () => {
    const changed = CONTENT_A.replace(QUOTE_A, '变'.repeat(characters(QUOTE_A)));
    const requests = installApi(request => pathOf(request) === CHUNK_A_PATH ? json(chunkResponse(DOCUMENT_A, changed), 200, TRACE_A) : undefined);
    await prepare(); await click(sourceButton());
    expectNoFullSource(); expectNoTrace(); expectConfirmedAnswer();
    expect(within(sourceRegion()).getByRole('alert')).toBeTruthy(); expectOnlyAnswerWrites(requests);
  });

  it('停止来源读取不撤销已确认答案；随后新问题成功后，旧来源迟到也不能挂到新答案', async () => {
    const pending = deferred(); let answers = 0;
    const requests = installApi(request => {
      if (pathOf(request) === CHUNK_A_PATH) return pending.promise;
      if (pathOf(request) === ANSWERS && ++answers === 2) return json({ answer: ABSTENTION, abstained: true, citations: [], reranking: null });
    });
    await prepare(); await click(sourceButton());
    const old = chunkReads(requests)[0]; await click(screen.getByRole('button', { name: '停止读取来源' }));
    expect(old.signal.aborted).toBe(true); expectConfirmedAnswer(); expectNoFullSource();
    await submit(NEXT_QUESTION); expect(screen.getByText(ABSTENTION)).toBeTruthy();
    const before = requests.length; await resolve(pending, json(chunkResponse()));
    expect(requests).toHaveLength(before); expect(within(answerRegion()).getByText(NEXT_QUESTION)).toBeTruthy();
    expect(screen.queryByText(ANSWER)).toBeNull(); expectNoFullSource(); expectNoTrace(); expectOnlyAnswerWrites(requests, 2);
  });

  it('编辑问题不改变旧来源归属，显式新提交才立即清全文和旧结果', async () => {
    const pending = deferred(); let answers = 0;
    const requests = installApi(request => pathOf(request) === ANSWERS && ++answers === 2 ? pending.promise : undefined);
    await prepare(); await click(sourceButton());
    await act(async () => { fireEvent.change(screen.getByLabelText('问答问题'), { target: { value: NEXT_QUESTION } }); });
    expectConfirmedAnswer(); expect(sourceRegion().textContent).toContain(CONTENT_A);
    await click(screen.getByRole('button', { name: '生成回答' }));
    expectNoFullSource(); expect(screen.queryByText(ANSWER)).toBeNull();
    expect(screen.queryByRole('region', { name: '引用 1 的完整来源段落' })).toBeNull();
    await resolve(pending, json({ answer: ABSTENTION, abstained: true, citations: [], reranking: null }));
    expect(within(answerRegion()).getByText(NEXT_QUESTION)).toBeTruthy(); expectOnlyAnswerWrites(requests, 2);
  });

  it.each(['back', 'logout-failed', 'expiry'] as const)('来源等待中%s清来源并隔离迟到错误和编号', async action => {
    const pending = deferred(), exiting = deferred();
    const requests = installApi(request => {
      if (pathOf(request) === RESTORE && action === 'expiry') return sessionResponse(Date.now() / 1000 + 2);
      if (pathOf(request) === CHUNK_A_PATH) return pending.promise;
      if (pathOf(request) === LOGOUT && action === 'logout-failed') return exiting.promise;
    });
    await prepare(); await click(sourceButton()); const old = chunkReads(requests)[0];
    if (action === 'back') await click(screen.getByRole('button', { name: '返回文档列表' }));
    else if (action === 'expiry') await advance(2000);
    else {
      await click(screen.getByRole('button', { name: '退出登录' }));
      expect(screen.queryByRole('region', { name: '引用 1 的完整来源段落' })).toBeNull();
      await resolve(exiting, json({ detail: PRIVATE }, 503, TRACE_A));
      expect(document.body.textContent).toContain('退出尚未确认'); expectConfirmedAnswer();
    }
    expect(old.signal.aborted).toBe(true); const before = requests.length;
    await resolve(pending, json({ detail: PRIVATE }, 503, TRACE_B));
    expect(requests).toHaveLength(before); expectNoFullSource(); expectNoTrace();
    expect(screen.queryByRole('region', { name: '引用 1 的完整来源段落' })).toBeNull();
    if (action === 'back') expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    if (action === 'expiry') expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expectOnlyAnswerWrites(requests);
  });

  it('手动来源详情GET的401清身份与答案，不在登录页保留正文或编号', async () => {
    let documentReads = 0;
    const requests = installApi(request => pathOf(request) === DETAIL_A && ++documentReads > 1
      ? json({ detail: PRIVATE }, 401, TRACE_A) : undefined);
    await prepare(); await click(sourceButton());
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByText(ANSWER)).toBeNull(); expectNoFullSource(); expectNoTrace();
    expect(chunkReads(requests)).toHaveLength(0); expectOnlyAnswerWrites(requests);
  });
});
