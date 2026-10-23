import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';
import * as exportModule from './answer-export';

const USER = '00000000-0000-0000-0000-000000000001';
const SPACE = '00000000-0000-0000-0000-00000000000a';
const KB = '00000000-0000-0000-0000-000000000101';
const DOCUMENT = '00000000-0000-0000-0000-000000000301';
const CHUNK = '00000000-0000-0000-0000-000000000501';
const RESTORE = '/api/v1/auth/web/session', LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces', KNOWLEDGE = `${SPACES}/${SPACE}/knowledge-bases`;
const DOCUMENTS = `${KNOWLEDGE}/${KB}/documents`, ANSWERS = `${KNOWLEDGE}/${KB}/answers`;
const DETAIL = `${DOCUMENTS}/${DOCUMENT}`, CHUNK_PATH = `${DETAIL}/chunks/${CHUNK}`;
const FILENAME = '虚构导出说明.md';
const QUESTION = '资料导出需要保留什么信息？', NEXT_QUESTION = '还有其他要求吗？';
const ANSWER = '导出资料需要保留名称与日期。';
const ABSTENTION = '目前检索到的资料不足以确认这个问题的答案。';
const QUOTE = '资料需要保留名称与日期🙂。';
const PREFIX = '仅用于来源预览的前置正文。'.repeat(30);
const SUFFIX = '不属于引文的来源正文末尾。'.repeat(30);
const CONTENT = `${PREFIX}${QUOTE}${SUFFIX}`;
const PRIVATE = 'PRIVATE_RESPONSE_OR_DOWNLOAD_ERROR_MUST_NOT_BE_EXPORTED';
const TRACE = 'a0e0b901-2b0d-4ae0-812a-bab2cf9e7b01';
const EXPORT = '导出回答与引文';
const REQUESTED = '已请求浏览器下载，请在下载列表中确认文件。';
const FAILED = '未能开始下载，请重试导出。原回答仍保留。';
type Route = (request: Request) => Response | Promise<Response> | undefined;

const characters = (value: string) => Array.from(value).length;
const pathOf = (request: Request) => new URL(request.url).pathname;
function json(body: unknown, status = 200, trace?: string) {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (trace) headers.set('X-Trace-Id', trace);
  return new Response(JSON.stringify(body), { status, headers });
}
function sessionResponse(expiresAt = Date.now() / 1000 + 1800) {
  return json({ user: { id: USER, username: 'demo_export_user' }, expires_at: expiresAt });
}
function documentRow() {
  return { id: DOCUMENT, knowledge_base_id: KB, original_filename: FILENAME, status: 'ready',
    internal_detail: PRIVATE };
}
function answerResult(abstained = false) {
  return { answer: abstained ? ABSTENTION : ANSWER, abstained, reranking: null,
    citations: abstained ? [] : [{ document_id: DOCUMENT, document_name: FILENAME, chunk_id: CHUNK,
      chunk_index: 1, quote: QUOTE, start_char: characters(PREFIX), end_char: characters(PREFIX + QUOTE) }] };
}
function expectedInput(question = QUESTION, abstained = false, submittedStrategy: 'vector' | 'hybrid' = 'vector') {
  return { workspaceName: '甲空间', knowledgeBaseName: '甲知识库', question, submittedStrategy,
    result: { answer: abstained ? ABSTENTION : ANSWER, abstained,
      citations: abstained ? [] : [{ documentId: DOCUMENT, documentName: FILENAME, chunkId: CHUNK,
        chunkIndex: 1, quote: QUOTE, startChar: characters(PREFIX), endChar: characters(PREFIX + QUOTE) }] } };
}
function chunkResponse() {
  return { id: CHUNK, document_id: DOCUMENT, chunk_index: 1,
    content: CONTENT, start_char: 0, end_char: characters(CONTENT) };
}
function deferred() {
  let resolve!: (response: Response) => void;
  return { promise: new Promise<Response>(done => { resolve = done; }), resolve: (response: Response) => resolve(response) };
}
function installApi(route: Route = () => undefined) {
  const requests: Request[] = [];
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const request = new Request(new URL(String(input), 'http://localhost'), init);
    requests.push(request);
    const response = route(request); if (response !== undefined) return response;
    switch (pathOf(request)) {
      case RESTORE: return sessionResponse();
      case LOGOUT: return new Response(null, { status: 204 });
      case SPACES: return json([{ id: SPACE, name: '甲空间' }]);
      case KNOWLEDGE: return json([{ id: KB, workspace_id: SPACE, name: '甲知识库' }]);
      case DOCUMENTS: return json([documentRow()]);
      case DETAIL: return json(documentRow());
      case CHUNK_PATH: return json(chunkResponse());
      case ANSWERS: return json(answerResult());
      default: throw new Error('Unexpected answer-export fixture endpoint');
    }
  }));
  return requests;
}
async function advance(milliseconds = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
}
async function click(element: HTMLElement) {
  await act(async () => { fireEvent.click(element); await vi.advanceTimersByTimeAsync(0); });
}
async function fillQuestion(question: string) {
  await act(async () => { fireEvent.change(screen.getByLabelText('问答问题'), { target: { value: question } }); });
}
function strategyInput() { return screen.getByLabelText<HTMLSelectElement>('问答检索方式'); }
async function chooseStrategy(strategy: 'vector' | 'hybrid') {
  await act(async () => { fireEvent.change(strategyInput(), { target: { value: strategy } }); });
}
async function submit(question = QUESTION) {
  await fillQuestion(question); await click(screen.getByRole('button', { name: '生成回答' }));
}
async function resolve(pending: ReturnType<typeof deferred>, response: Response) {
  await act(async () => { pending.resolve(response); await vi.advanceTimersByTimeAsync(0); });
}
async function openAnswers() {
  render(<App />); await advance(); await click(screen.getByRole('button', { name: '进入甲空间' }));
  await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
  await click(screen.getByRole('button', { name: '引用问答' }));
}
async function prepare(strategy: 'vector' | 'hybrid' = 'vector') {
  await openAnswers();
  if (strategy === 'hybrid') await chooseStrategy(strategy);
  await submit();
}
function exportButton() { return screen.getByRole('button', { name: EXPORT }) as HTMLButtonElement; }
function sourceButton() { return screen.getByRole('button', { name: '查看引用 1 的完整来源段落' }); }
function resultRegion() { return screen.getByRole('region', { name: '本次问答结果' }); }
function expectOriginalAnswer() {
  expect(within(resultRegion()).getByText(QUESTION)).toBeTruthy();
  expect(within(resultRegion()).getByText(ANSWER)).toBeTruthy();
  expect(within(resultRegion()).getByText(QUOTE)).toBeTruthy();
}
function expectNoExportStatus() {
  expect(screen.queryByText(REQUESTED)).toBeNull(); expect(screen.queryByText(FAILED)).toBeNull();
}
function expectNoEnabledExport() {
  const button = screen.queryByRole('button', { name: EXPORT }) as HTMLButtonElement | null;
  expect(button === null || button.disabled).toBe(true);
}

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
  vi.spyOn(exportModule, 'downloadAnswerText').mockImplementation(() => undefined);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('已确认引用问答的显式本地文本导出', () => {
  it('只有点击才导出，编辑问题和向量草稿不改变混合提交快照或增加请求', async () => {
    const requests = installApi(); await prepare('hybrid');
    expectOriginalAnswer(); expect(exportModule.downloadAnswerText).not.toHaveBeenCalled();
    await fillQuestion(NEXT_QUESTION);
    await chooseStrategy('vector');
    expect(resultRegion().textContent).toContain('本次提交的检索方式：混合检索（向量＋词项）');
    const before = requests.length; await click(exportButton());
    expect(exportModule.downloadAnswerText).toHaveBeenCalledExactlyOnceWith(expectedInput(QUESTION, false, 'hybrid'));
    const input = vi.mocked(exportModule.downloadAnswerText).mock.calls[0][0];
    expect(exportModule.buildAnswerText(input)).toContain('混合检索（向量＋词项）');
    expect(exportModule.buildAnswerText(input)).not.toContain(NEXT_QUESTION);
    expect(screen.getByText(REQUESTED)).toBeTruthy(); expectOriginalAnswer();
    await advance(30_000); expect(requests).toHaveLength(before);
    expect(requests.filter(request => request.method === 'POST' && pathOf(request) === ANSWERS)).toHaveLength(1);
    expect(await requests.find(request => pathOf(request) === ANSWERS)!.clone().json())
      .toEqual({ question: QUESTION, top_k: 3, strategy: 'hybrid', rerank: false });
    expect(JSON.stringify(vi.mocked(exportModule.downloadAnswerText).mock.calls[0])).not.toContain('demo_export_user');
    expect(JSON.stringify(vi.mocked(exportModule.downloadAnswerText).mock.calls[0])).not.toContain(PRIVATE);
  });

  it('混合问答正常弃答也可导出提交问题、策略和零条引用，不新增来源读取', async () => {
    const requests = installApi(request => pathOf(request) === ANSWERS ? json(answerResult(true)) : undefined);
    await prepare('hybrid'); expect(screen.getByRole('heading', { name: '证据不足，本次弃答' })).toBeTruthy();
    const before = requests.length; await click(exportButton());
    expect(exportModule.downloadAnswerText).toHaveBeenCalledExactlyOnceWith(expectedInput(QUESTION, true, 'hybrid'));
    expect(resultRegion().textContent).toContain('本次提交的检索方式：混合检索（向量＋词项）');
    expect(screen.getByText(REQUESTED)).toBeTruthy(); expect(requests).toHaveLength(before);
    expect(requests.filter(request => [DETAIL, CHUNK_PATH].includes(pathOf(request)))).toHaveLength(0);
  });

  it.each(['rejected', 'invalid-result', 'network'] as const)('%s结果没有导出入口，也不能把故障包装为弃答', async problem => {
    installApi(request => {
      if (pathOf(request) !== ANSWERS) return;
      if (problem === 'rejected') return json({ detail: PRIVATE }, 403, TRACE);
      if (problem === 'invalid-result') return json({ ...answerResult(), citations: [] });
      return Promise.reject(new TypeError(PRIVATE));
    });
    await openAnswers(); expect(screen.queryByRole('button', { name: EXPORT })).toBeNull();
    await submit();
    expect(screen.queryByRole('button', { name: EXPORT })).toBeNull();
    expect(exportModule.downloadAnswerText).not.toHaveBeenCalled(); expectNoExportStatus();
    expect(screen.queryByRole('heading', { name: '证据不足，本次弃答' })).toBeNull();
    expect(document.body.textContent).not.toContain(PRIVATE);
  });

  it('来源等待禁导出和方式选择；GET失败后仍导出原混合快照，不附预览、错误或编号', async () => {
    const pending = deferred();
    const requests = installApi(request => pathOf(request) === CHUNK_PATH ? pending.promise : undefined);
    await prepare('hybrid'); await fillQuestion(NEXT_QUESTION); await chooseStrategy('vector'); await click(sourceButton());
    expect(strategyInput().disabled).toBe(true);
    expect(exportButton().disabled).toBe(true); await click(exportButton());
    expect(exportModule.downloadAnswerText).not.toHaveBeenCalled();
    await resolve(pending, json({ detail: PRIVATE }, 503, TRACE));
    expectOriginalAnswer(); expect(screen.getByText(TRACE)).toBeTruthy(); expect(exportButton().disabled).toBe(false);
    expect(strategyInput().disabled).toBe(false); expect(strategyInput().value).toBe('vector');
    expect(resultRegion().textContent).toContain('本次提交的检索方式：混合检索（向量＋词项）');
    const before = requests.length; await click(exportButton());
    expect(exportModule.downloadAnswerText).toHaveBeenCalledExactlyOnceWith(expectedInput(QUESTION, false, 'hybrid'));
    expect(requests).toHaveLength(before); expectOriginalAnswer();
    const input = JSON.stringify(vi.mocked(exportModule.downloadAnswerText).mock.calls[0]);
    for (const value of [PREFIX, SUFFIX, TRACE, PRIVATE, 'source', 'traceId']) expect(input).not.toContain(value);
  });

  it('来源GET成功与关闭不改变混合提交策略，完整块不混入导出或触发新POST', async () => {
    const requests = installApi(); await prepare('hybrid');
    await fillQuestion(NEXT_QUESTION); await chooseStrategy('vector'); await click(sourceButton());
    expect(screen.getByLabelText('完整来源正文').textContent).toContain(CONTENT);
    const before = requests.length; await click(exportButton());
    expect(exportModule.downloadAnswerText).toHaveBeenCalledExactlyOnceWith(expectedInput(QUESTION, false, 'hybrid'));
    await click(screen.getByRole('button', { name: '关闭来源段落' }));
    expect(screen.getByText(REQUESTED)).toBeTruthy(); expect(requests).toHaveLength(before);
    expect(resultRegion().textContent).toContain('本次提交的检索方式：混合检索（向量＋词项）');
    await click(exportButton());
    expect(exportModule.downloadAnswerText).toHaveBeenNthCalledWith(2, expectedInput(QUESTION, false, 'hybrid'));
    expect(requests).toHaveLength(before);
    expect(requests.filter(request => request.method === 'POST' && pathOf(request) === ANSWERS)).toHaveLength(1);
    expectOriginalAnswer();
  });

  it('下载异常使用安全提示且保留答案与引文，用户可再次显式导出', async () => {
    const download = vi.mocked(exportModule.downloadAnswerText);
    download.mockImplementationOnce(() => { throw new Error(PRIVATE); });
    const requests = installApi(); await prepare(); const before = requests.length;
    await click(exportButton()); expectOriginalAnswer(); expect(screen.getByText(FAILED)).toBeTruthy();
    expect(screen.queryByText(REQUESTED)).toBeNull(); expect(document.body.textContent).not.toContain(PRIVATE);
    expect(exportButton().disabled).toBe(false); await advance(30_000);
    expect(download).toHaveBeenCalledTimes(1); await click(exportButton());
    expect(download).toHaveBeenCalledTimes(2);
    expect(download.mock.calls[0]).toEqual([expectedInput()]); expect(download.mock.calls[1]).toEqual([expectedInput()]);
    expect(screen.getByText(REQUESTED)).toBeTruthy(); expect(screen.queryByText(FAILED)).toBeNull();
    expectOriginalAnswer(); expect(requests).toHaveLength(before);
  });

  it('新问题立即清旧导出入口与提示，取消等待后也不能导出旧结果', async () => {
    const next = deferred(); let answers = 0;
    installApi(request => pathOf(request) === ANSWERS && ++answers === 2 ? next.promise : undefined);
    await prepare(); await click(exportButton()); expect(screen.getByText(REQUESTED)).toBeTruthy();
    await submit(NEXT_QUESTION);
    expect(screen.queryByRole('button', { name: EXPORT })).toBeNull(); expectNoExportStatus();
    await click(screen.getByRole('button', { name: '停止等待回答结果' }));
    expect(screen.queryByRole('button', { name: EXPORT })).toBeNull();
    await resolve(next, json(answerResult()));
    expect(screen.queryByRole('button', { name: EXPORT })).toBeNull(); expectNoExportStatus();
    expect(exportModule.downloadAnswerText).toHaveBeenCalledTimes(1);
  });

  it('第二次向量提交清旧导出，确认后导出新问题与方式，不能沿用原混合快照', async () => {
    const next = deferred(); let answers = 0;
    const requests = installApi(request => pathOf(request) === ANSWERS && ++answers === 2 ? next.promise : undefined);
    await prepare('hybrid'); await click(exportButton());
    expect(exportModule.downloadAnswerText).toHaveBeenCalledExactlyOnceWith(expectedInput(QUESTION, false, 'hybrid'));
    await chooseStrategy('vector'); await submit(NEXT_QUESTION);
    expect(screen.queryByRole('button', { name: EXPORT })).toBeNull(); expectNoExportStatus();
    await resolve(next, json(answerResult(true)));
    expect(resultRegion().textContent).toContain('本次提交的检索方式：向量检索');
    expect(resultRegion().textContent).not.toContain(QUESTION);
    const before = requests.length; await click(exportButton());
    expect(exportModule.downloadAnswerText).toHaveBeenNthCalledWith(2, expectedInput(NEXT_QUESTION, true, 'vector'));
    const input = vi.mocked(exportModule.downloadAnswerText).mock.calls[1][0];
    expect(exportModule.buildAnswerText(input)).toContain(NEXT_QUESTION);
    expect(exportModule.buildAnswerText(input)).not.toContain('混合检索（向量＋词项）');
    const posts = requests.filter(request => request.method === 'POST' && pathOf(request) === ANSWERS);
    expect(posts).toHaveLength(2);
    expect(await posts[0].clone().json()).toEqual({ question: QUESTION, top_k: 3, strategy: 'hybrid', rerank: false });
    expect(await posts[1].clone().json()).toEqual({ question: NEXT_QUESTION, top_k: 3, strategy: 'vector', rerank: false });
    expect(requests).toHaveLength(before);
  });

  it('返回文档再进入问答不保留旧结果、下载提示或导出入口', async () => {
    installApi(); await prepare('hybrid'); await click(exportButton());
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(screen.queryByRole('button', { name: EXPORT })).toBeNull(); expectNoExportStatus();
    await click(screen.getByRole('button', { name: '引用问答' }));
    expect(strategyInput().value).toBe('vector');
    expect(screen.queryByRole('region', { name: '本次问答结果' })).toBeNull();
    expect(screen.queryByRole('button', { name: EXPORT })).toBeNull(); expectNoExportStatus();
    expect(exportModule.downloadAnswerText).toHaveBeenCalledTimes(1);
  });

  it('来源401清身份和旧导出结果，不在登录页保留答案或下载状态', async () => {
    installApi(request => pathOf(request) === CHUNK_PATH ? json({ detail: PRIVATE }, 401, TRACE) : undefined);
    await prepare(); await click(exportButton()); await click(sourceButton());
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: EXPORT })).toBeNull(); expectNoExportStatus();
    expect(screen.queryByText(ANSWER)).toBeNull(); expect(document.body.textContent).not.toContain(TRACE);
    expect(exportModule.downloadAnswerText).toHaveBeenCalledTimes(1);
  });

  it('退出进行中和失败后均不能继续导出旧结果，也不保留先前的下载状态', async () => {
    const exiting = deferred();
    installApi(request => pathOf(request) === LOGOUT ? exiting.promise : undefined);
    await prepare(); await click(exportButton()); await click(screen.getByRole('button', { name: '退出登录' }));
    expectNoEnabledExport(); expectNoExportStatus();
    await resolve(exiting, json({ detail: PRIVATE }, 503, TRACE));
    expect(document.body.textContent).toContain('退出尚未确认'); expectNoEnabledExport(); expectNoExportStatus();
    const disabled = screen.queryByRole('button', { name: EXPORT }); if (disabled) await click(disabled);
    expect(exportModule.downloadAnswerText).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).not.toContain(PRIVATE);
  });

  it('模拟固定会话到期清旧结果与下载状态，不需要真实等待或发新问答', async () => {
    const requests = installApi(request => pathOf(request) === RESTORE ? sessionResponse(Date.now() / 1000 + 2) : undefined);
    await prepare(); await click(exportButton()); const before = requests.length;
    await advance(2000);
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: EXPORT })).toBeNull(); expectNoExportStatus();
    expect(screen.queryByText(ANSWER)).toBeNull(); expect(exportModule.downloadAnswerText).toHaveBeenCalledTimes(1);
    expect(requests).toHaveLength(before);
  });
});
