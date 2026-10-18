import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';

const USER = '00000000-0000-0000-0000-000000000001';
const SPACE = '00000000-0000-0000-0000-00000000000a';
const KB = '00000000-0000-0000-0000-000000000101';
const DOCUMENT = '00000000-0000-0000-0000-000000000301';
const CHUNK = '00000000-0000-0000-0000-000000000501';
const TRACE_A = 'a0e0b901-2b0d-4ae0-812a-bab2cf9e7b01';
const TRACE_B = 'b1f1c012-3c1e-4bf1-923b-cbc3dfa08c12';
const RESTORE = '/api/v1/auth/web/session';
const LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces';
const KNOWLEDGE = `${SPACES}/${SPACE}/knowledge-bases`;
const DOCUMENTS = `${KNOWLEDGE}/${KB}/documents`;
const DETAIL = `${DOCUMENTS}/${DOCUMENT}`;
const ANSWERS = `${KNOWLEDGE}/${KB}/answers`;
const QUESTION = '导出条目包含什么？';
const ANSWER = '导出条目包含文件名称。';
const FILENAME = '导出说明.md';
const PRIVATE = 'PRIVATE_BODY_OR_EXCEPTION_MUST_NOT_BE_DISPLAYED';

type Route = (request: Request) => Response | Promise<Response> | undefined;

function json(body: unknown, status = 200, trace?: string) {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (trace !== undefined) headers.set('X-Trace-Id', trace);
  return new Response(JSON.stringify(body), { status, headers });
}

function documentRow(knowledgeBaseId = KB) {
  return { id: DOCUMENT, knowledge_base_id: knowledgeBaseId, original_filename: FILENAME, status: 'ready', error_code: null };
}

function answerResult() {
  return { answer: ANSWER, abstained: false, reranking: null, citations: [{
    chunk_id: CHUNK, document_id: DOCUMENT, document_name: FILENAME, chunk_index: 1,
    quote: ANSWER, start_char: 0, end_char: Array.from(ANSWER).length,
  }] };
}

function failure(status = 502, trace: string | undefined = TRACE_A) {
  // 正文故意包含另一个合法编号，页面只能信任经过校验的响应头。
  return json({ detail: PRIVATE, trace_id: TRACE_B }, status, trace);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function pathOf(request: Request) { return new URL(request.url).pathname; }
function answerPosts(requests: Request[]) {
  return requests.filter(request => request.method === 'POST' && pathOf(request) === ANSWERS);
}

// 所有请求都由真实 Response 模拟；迟到响应故意忽略 abort，检查 App 的状态隔离。
function installApi(route: Route = () => undefined) {
  const requests: Request[] = [];
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request
      ? new Request(input, init)
      : new Request(new URL(String(input), 'http://localhost'), init);
    requests.push(request);
    const response = route(request);
    if (response !== undefined) return response;
    switch (pathOf(request)) {
      case RESTORE:
      case '/api/v1/auth/web/login':
        return json({ user: { id: USER, username: 'demo_a' }, expires_at: Date.now() / 1000 + 1800 });
      case LOGOUT: return new Response(null, { status: 204 });
      case SPACES: return json([{ id: SPACE, name: '甲空间' }]);
      case KNOWLEDGE: return json([{ id: KB, workspace_id: SPACE, name: '甲知识库' }]);
      case DOCUMENTS: return json([documentRow()]);
      case DETAIL: return json(documentRow());
      case ANSWERS: return json(answerResult());
      default: throw new Error('Unexpected mocked endpoint');
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

async function openAnswers() {
  await advance();
  await click(screen.getByRole('button', { name: '进入甲空间' }));
  await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
  await click(screen.getByRole('button', { name: '引用问答' }));
}

async function submitQuestion(value = QUESTION) {
  await act(async () => { fireEvent.change(screen.getByLabelText('问答问题'), { target: { value } }); });
  await click(screen.getByRole('button', { name: '生成回答' }));
}

function expectNoTrace() {
  expect(document.body.textContent).not.toContain('排错编号：');
  expect(document.body.textContent).not.toContain(TRACE_A);
  expect(document.body.textContent).not.toContain(TRACE_B);
}

function expectSafeFailure() {
  expect(screen.getAllByRole('alert').length).toBeGreaterThan(0);
  expect(document.body.textContent).not.toContain(PRIVATE);
  expect(document.body.textContent).not.toContain(ANSWER);
  expect(screen.queryByRole('heading', { name: '证据不足，本次弃答' })).toBeNull();
}

function expectTrace(trace: string) {
  expect(document.body.textContent).toContain('排错编号：');
  expect(screen.getByText(trace).tagName).toBe('CODE');
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('独立引用问答页的排错编号', () => {
  it.each([409, 502])('POST %i 在安全错误下显示响应头编号，不读取错误正文或自动重发', async status => {
    const requests = installApi(request => pathOf(request) === ANSWERS ? failure(status) : undefined);
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expectSafeFailure();
    expectTrace(TRACE_A);
    expect(document.body.textContent).not.toContain(TRACE_B);
    await advance(30_000);
    expect(answerPosts(requests)).toHaveLength(1);
    expect(requests.some(request => pathOf(request) === DETAIL)).toBe(false);
    for (const request of requests) expect(request.headers.has('X-Trace-Id')).toBe(false);
    expect(await answerPosts(requests)[0].clone().json()).toEqual({
      question: QUESTION, top_k: 3, strategy: 'vector', rerank: false,
    });
  });

  it('POST 成功 A 后来源 GET 失败 B，只显示失败 GET 的编号 B，整批答案仍隐藏', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return json(answerResult(), 200, TRACE_A);
      if (pathOf(request) === DETAIL) return failure(404, TRACE_B);
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expectSafeFailure();
    expectTrace(TRACE_B);
    expect(document.body.textContent).not.toContain(TRACE_A);
    expect(document.body.textContent).toContain('问答结果未确认');
    expect(answerPosts(requests)).toHaveLength(1);
    expect(requests.filter(request => pathOf(request) === DETAIL)).toHaveLength(1);
  });

  it.each([
    undefined,
    'not-a-uuid',
    'a0e0b901-2b0d-1ae0-812a-bab2cf9e7b01',
    `${TRACE_A}, ${TRACE_B}`,
    '0'.repeat(1000),
  ])('缺失或非法响应头 %s 不显示编号，也不从错误正文补取', async trace => {
    installApi(request => pathOf(request) === ANSWERS ? json({ detail: PRIVATE, trace_id: TRACE_B }, 502, trace) : undefined);
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expectSafeFailure();
    expectNoTrace();
  });

  it.each(['network', 'invalid-answer', 'invalid-source'])('%s 没有已确认的失败响应编号时不伪造或沿用成功编号', async problem => {
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) {
        if (problem === 'network') return Promise.reject(new TypeError(`${PRIVATE} ${TRACE_A}`));
        return json(problem === 'invalid-answer' ? { answer: PRIVATE } : answerResult(), 200, TRACE_A);
      }
      if (problem === 'invalid-source' && pathOf(request) === DETAIL) {
        return json(documentRow('00000000-0000-0000-0000-000000000102'), 200, TRACE_B);
      }
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expectSafeFailure();
    expectNoTrace();
    expect(document.body.textContent).toContain('问答结果未确认');
    await advance(30_000);
    expect(answerPosts(requests)).toHaveLength(1);
  });

  it('成功回答及成功来源响应上的编号不作为回答字段展示', async () => {
    installApi(request => {
      if (pathOf(request) === ANSWERS) return json(answerResult(), 200, TRACE_A);
      if (pathOf(request) === DETAIL) return json(documentRow(), 200, TRACE_B);
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expect(screen.getByRole('region', { name: '本次问答结果' }).textContent).toContain(ANSWER);
    expect(screen.queryByRole('alert')).toBeNull();
    expectNoTrace();
  });

  it('再次提交立即清旧编号，取消后迟到失败编号不能覆盖未确认提示', async () => {
    const pending = deferred<Response>();
    let submissions = 0;
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return ++submissions === 1 ? failure() : pending.promise;
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expectTrace(TRACE_A);
    await submitQuestion('另一个问题');
    expectNoTrace();
    await click(screen.getByRole('button', { name: '停止等待回答结果' }));
    expect(answerPosts(requests)[1].signal.aborted).toBe(true);
    await act(async () => { pending.resolve(failure(502, TRACE_B)); await vi.advanceTimersByTimeAsync(0); });
    expectNoTrace();
    expectSafeFailure();
    expect(document.body.textContent).toContain('问答结果未确认');
    expect(answerPosts(requests)).toHaveLength(2);
  });

  it('返回文档再进入清除旧编号，已离开页面的迟到失败不能污染新页面', async () => {
    const pending = deferred<Response>();
    let submissions = 0;
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return ++submissions === 1 ? failure() : pending.promise;
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expectTrace(TRACE_A);
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expectNoTrace();
    await click(screen.getByRole('button', { name: '引用问答' }));
    expectNoTrace();
    await submitQuestion();
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(answerPosts(requests)[1].signal.aborted).toBe(true);
    await click(screen.getByRole('button', { name: '引用问答' }));
    await act(async () => { pending.resolve(failure(502, TRACE_B)); await vi.advanceTimersByTimeAsync(0); });
    expectNoTrace();
    expect(screen.queryByRole('alert')).toBeNull();
    expect((screen.getByLabelText('问答问题') as HTMLTextAreaElement).value).toBe('');
    expect(answerPosts(requests)).toHaveLength(2);
  });

  it('退出失败后仍清除已展示的问答编号，不将退出请求编号混入问答结果', async () => {
    installApi(request => {
      if (pathOf(request) === ANSWERS) return failure();
      if (pathOf(request) === LOGOUT) return failure(503, TRACE_B);
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expectTrace(TRACE_A);
    await click(screen.getByRole('button', { name: '退出登录' }));
    expect(document.body.textContent).toContain('退出尚未确认');
    expectNoTrace();
    expect(document.body.textContent).not.toContain(PRIVATE);
  });

  it.each([ANSWERS, DETAIL])('问答或来源 401 清空旧编号及身份，不在登录页展示新编号（%s）', async endpoint => {
    let submissions = 0;
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) {
        if (++submissions === 1) return failure();
        return endpoint === ANSWERS ? failure(401, TRACE_B) : json(answerResult(), 200, TRACE_A);
      }
      if (pathOf(request) === endpoint) return failure(401, TRACE_B);
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expectTrace(TRACE_A);
    await submitQuestion();
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expectNoTrace();
    expect(screen.queryByLabelText('问答问题')).toBeNull();
    expect(document.body.textContent).not.toContain(PRIVATE);
    expect(answerPosts(requests)).toHaveLength(2);
  });
});
