import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';

const USER = '00000000-0000-0000-0000-000000000001';
const SPACE = '00000000-0000-0000-0000-00000000000a';
const KB = '00000000-0000-0000-0000-000000000101';
const OTHER_KB = '00000000-0000-0000-0000-000000000102';
const DOCUMENT_A = '00000000-0000-0000-0000-000000000301';
const DOCUMENT_B = '00000000-0000-0000-0000-000000000302';
const RESTORE = '/api/v1/auth/web/session';
const LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces';
const KNOWLEDGE = `${SPACES}/${SPACE}/knowledge-bases`;
const DOCUMENTS = `${KNOWLEDGE}/${KB}/documents`;
const OTHER_DOCUMENTS = `${KNOWLEDGE}/${OTHER_KB}/documents`;
const DETAIL_A = `${DOCUMENTS}/${DOCUMENT_A}`;
const DETAIL_B = `${DOCUMENTS}/${DOCUMENT_B}`;
const ANSWERS = `${KNOWLEDGE}/${KB}/answers`;
const FILENAME_A = '导出说明.md';
const FILENAME_B = '保留期限.md';
const PRIVATE = 'PRIVATE_ANSWER_DETAIL_MUST_NOT_BE_DISPLAYED';
const QUESTION = '一个导出条目包含什么信息？';
const ANSWER = '导出条目包含文件名称、创建时间与状态，保留期限见引用资料。';
const ABSTENTION = '目前检索到的资料不足以确认这个问题的答案。';
const UNSAFE_START = '<img src=x onerror="alert(1)">';
const LONG_QUOTE = `${UNSAFE_START}${'🙂'.repeat(305)}完整引文的结尾不能被省略`;
const SECOND_QUOTE = '同一份文档还记录创建时间与处理状态。';
const THIRD_QUOTE = '导出条目的保留期限为七天。';

type Route = (request: Request) => Response | Promise<Response> | undefined;

function json(body: unknown, status = 200, traceId?: string) {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (traceId) headers.set('X-Trace-Id', traceId);
  return new Response(JSON.stringify(body), { status, headers });
}

function sessionResponse(expiresAt = Date.now() / 1000 + 1800) {
  return json({ user: { id: USER, username: 'demo_a' }, expires_at: expiresAt });
}

function documentRow(id = DOCUMENT_A, filename = FILENAME_A, knowledgeBaseId = KB, status = 'ready') {
  return { id, knowledge_base_id: knowledgeBaseId, original_filename: filename, status, error_code: PRIVATE };
}

function citation(index = 0) {
  const quote = [LONG_QUOTE, SECOND_QUOTE, THIRD_QUOTE][index];
  const start = [100, 460, 0][index];
  return {
    chunk_id: index === 2
      ? '00000000-0000-0000-0000-000000000503'
      : '00000000-0000-0000-0000-000000000501',
    document_id: index === 2 ? DOCUMENT_B : DOCUMENT_A,
    document_name: index === 2 ? FILENAME_B : FILENAME_A,
    chunk_index: 1,
    quote, start_char: start, end_char: start + Array.from(quote).length,
  };
}

function answerResult() {
  return { answer: ANSWER, abstained: false, citations: [citation(0), citation(1), citation(2)], reranking: null };
}

function abstentionResult() {
  return { answer: ABSTENTION, abstained: true, citations: [], reranking: null };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function pathOf(request: Request) { return new URL(request.url).pathname; }
function answerPosts(requests: Request[]) {
  return requests.filter(request => request.method === 'POST' && pathOf(request).endsWith('/answers'));
}
function sourceReads(requests: Request[]) {
  return requests.filter(request => request.method === 'GET' && [DETAIL_A, DETAIL_B].includes(pathOf(request)));
}

// 全部请求均为模拟；迟到响应故意不随 abort 消失，以检查页面自己的失效保护。
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
      case '/api/v1/auth/web/login': return sessionResponse();
      case LOGOUT: return new Response(null, { status: 204 });
      case SPACES: return json([{ id: SPACE, name: '甲空间' }]);
      case KNOWLEDGE: return json([
        { id: KB, workspace_id: SPACE, name: '甲知识库' },
        { id: OTHER_KB, workspace_id: SPACE, name: '乙知识库' },
      ]);
      // 第一页没有 ready 不能推导整库无可检索资料。
      case DOCUMENTS: return json([documentRow(DOCUMENT_A, FILENAME_A, KB, 'parsed')]);
      case OTHER_DOCUMENTS: return json([]);
      case DETAIL_A: return json(documentRow());
      case DETAIL_B: return json(documentRow(DOCUMENT_B, FILENAME_B));
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

function questionInput() { return screen.getByLabelText('问答问题') as HTMLTextAreaElement; }
function strategyInput() { return screen.getByLabelText<HTMLSelectElement>('问答检索方式'); }

async function chooseStrategy(strategy: 'vector' | 'hybrid') {
  await act(async () => { fireEvent.change(strategyInput(), { target: { value: strategy } }); });
}

async function fillQuestion(value: string) {
  await act(async () => { fireEvent.change(questionInput(), { target: { value } }); });
}

async function openDocuments() {
  await advance();
  await click(screen.getByRole('button', { name: '进入甲空间' }));
  await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
}

async function openAnswers() {
  await openDocuments();
  await click(screen.getByRole('button', { name: '引用问答' }));
}

async function submitQuestion(value = QUESTION) {
  await fillQuestion(value);
  await click(screen.getByRole('button', { name: '生成回答' }));
}

async function signIn() {
  await act(async () => {
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'demo_a' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'fake-answer-test-password' } });
  });
  await click(screen.getByRole('button', { name: '登录' }));
}

function expectNoPrivateOutput() {
  expect(document.body.textContent).not.toContain(PRIVATE);
  for (const id of [USER, SPACE, KB, OTHER_KB, DOCUMENT_A, DOCUMENT_B,
    ...[0, 1, 2].map(index => citation(index).chunk_id)]) {
    expect(document.body.textContent).not.toContain(id);
  }
}

function expectNoAnswerContent() {
  for (const value of [ANSWER, UNSAFE_START, SECOND_QUOTE, THIRD_QUOTE]) {
    expect(document.body.textContent).not.toContain(value);
  }
}

function expectUnconfirmed() {
  expect(document.body.textContent).toMatch(/问答结果未确认/);
  expect(document.body.textContent).toContain('未自动重试');
  expect(screen.queryByRole('heading', { name: '证据不足，本次弃答' })).toBeNull();
  expectNoAnswerContent();
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

describe('知识库独立引用问答页', () => {
  it('第一页零份可检索仍可进入，进入与编辑不发送请求，空白或超长问题本地拒绝', async () => {
    const requests = installApi();
    render(<App />);
    await openDocuments();
    expect(document.body.textContent).toContain('0 份可检索');
    const entry = screen.getByRole('button', { name: '引用问答' }) as HTMLButtonElement;
    expect(entry.disabled).toBe(false);
    await click(entry);
    expect(screen.getByRole('heading', { level: 1, name: '引用问答' })).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 1, name: '文档' })).toBeNull();
    expect(document.body.textContent).toContain('甲空间');
    expect(document.body.textContent).toContain('甲知识库');
    expect(questionInput().value).toBe('');
    expect(strategyInput().value).toBe('vector');
    expect(answerPosts(requests)).toHaveLength(0);
    for (const value of ['', ' \n\t ', '🙂'.repeat(2001)]) {
      await submitQuestion(value);
      expect(screen.getByRole('alert')).toBeTruthy();
      expect(answerPosts(requests)).toHaveLength(0);
    }
    await fillQuestion(QUESTION);
    await chooseStrategy('hybrid');
    expect(strategyInput().value).toBe('hybrid');
    expect(screen.getByText(/问答页与检索页分别选择方式/)).toBeTruthy();
    expect(screen.getByText(/切换方式不发送请求/)).toBeTruthy();
    expect(screen.getByText(/不限于文档列表页或文件名、状态筛选结果/)).toBeTruthy();
    expect(answerPosts(requests)).toHaveLength(0);
    expect(sourceReads(requests)).toHaveLength(0);
    expect(requests.some(request => pathOf(request).endsWith('/search'))).toBe(false);
    expectNoPrivateOutput();
  });

  it('文档读取中、读取失败与退出中均阻止问答入口', async () => {
    const documents = deferred<Response>();
    const exiting = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === DOCUMENTS) return documents.promise;
      if (pathOf(request) === LOGOUT) return exiting.promise;
    });
    render(<App />);
    await openDocuments();
    expect((screen.getByRole('button', { name: '引用问答' }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { documents.resolve(json({ detail: PRIVATE }, 404)); await vi.advanceTimersByTimeAsync(0); });
    expect((screen.getByRole('button', { name: '引用问答' }) as HTMLButtonElement).disabled).toBe(true);
    await click(screen.getByRole('button', { name: '退出登录' }));
    expect((screen.getByRole('button', { name: '引用问答' }) as HTMLButtonElement).disabled).toBe(true);
    expect(answerPosts(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it('上传尚未完成时不能进入问答或发送生成请求', async () => {
    const bytes = deferred<ArrayBuffer>();
    const file = new File(['模拟资料'], '上传中的资料.md', { type: 'text/markdown' });
    Object.defineProperty(file, 'arrayBuffer', { value: () => bytes.promise });
    const requests = installApi();
    render(<App />);
    await openDocuments();
    await act(async () => { fireEvent.change(screen.getByLabelText('选择文档'), { target: { files: [file] } }); });
    await click(screen.getByRole('button', { name: '上传' }));
    expect(screen.getByRole('status').textContent).toContain('正在上传并解析');
    const entry = screen.getByRole('button', { name: '引用问答' }) as HTMLButtonElement;
    expect(entry.disabled).toBe(true);
    await click(entry);
    expect(screen.queryByLabelText('问答问题')).toBeNull();
    expect(answerPosts(requests)).toHaveLength(0);
  });

  it.each(['vector', 'hybrid'] as const)('%s仅一次Cookie问答POST，全来源核验完成后才显示答案及提交方式快照', async strategy => {
    const pendingAnswer = deferred<Response>();
    const secondSource = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return pendingAnswer.promise;
      if (pathOf(request) === DETAIL_B) return secondSource.promise;
    });
    const { container } = render(<App />);
    await openAnswers();
    if (strategy === 'hybrid') await chooseStrategy(strategy);
    await submitQuestion(`\u3000 ${QUESTION}\n`);
    const running = screen.getByRole('button', { name: '正在回答…' }) as HTMLButtonElement;
    expect(running.disabled).toBe(true);
    expect(questionInput().disabled).toBe(true);
    expect(strategyInput().disabled).toBe(true);
    await click(running);
    expect(answerPosts(requests)).toHaveLength(1);
    const [post] = answerPosts(requests);
    expect(pathOf(post)).toBe(ANSWERS);
    expect(await post.clone().json()).toEqual({ question: QUESTION, top_k: 3, strategy, rerank: false });
    expect(post.credentials).toBe('same-origin');
    expect(post.headers.get('X-EAAP-Web')).toBe('1');
    expect(post.headers.get('Content-Type')).toBe('application/json');
    expect(post.headers.has('Authorization')).toBe(false);
    expect(requests.some(request => pathOf(request).endsWith('/search'))).toBe(false);
    await act(async () => { pendingAnswer.resolve(json(answerResult())); await vi.advanceTimersByTimeAsync(0); });
    expect(sourceReads(requests).map(pathOf)).toEqual([DETAIL_A, DETAIL_B]);
    expectNoAnswerContent();
    expect(screen.queryByRole('region', { name: '本次问答结果' })).toBeNull();
    await act(async () => { secondSource.resolve(json(documentRow(DOCUMENT_B, FILENAME_B))); await vi.advanceTimersByTimeAsync(0); });
    const result = screen.getByRole('region', { name: '本次问答结果' });
    expect(result.textContent).toContain(`本次提交的检索方式：${strategy === 'hybrid' ? '混合检索（向量＋词项）' : '向量检索'}`);
    expect(result.textContent).toContain('未请求重排');
    expect(result.querySelectorAll('blockquote')).toHaveLength(3);
    for (const value of [QUESTION, ANSWER, FILENAME_A, FILENAME_B, LONG_QUOTE, SECOND_QUOTE, THIRD_QUOTE]) {
      expect(result.textContent).toContain(value);
    }
    expect(result.textContent).toMatch(/\[100,\s*\d+\)/);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    for (const request of sourceReads(requests)) {
      expect(request.credentials).toBe('same-origin');
      expect(request.headers.get('X-EAAP-Web')).toBe('1');
      expect(request.headers.has('Authorization')).toBe(false);
    }
    expect(sourceReads(requests).filter(request => pathOf(request) === DETAIL_A)).toHaveLength(1);
    expect(answerPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('2000个Unicode字符可提交，首尾空白不计入问题长度或正文', async () => {
    const question = '🙂'.repeat(2000);
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return json(abstentionResult());
    });
    render(<App />);
    await openAnswers();
    await submitQuestion(` \n${question}\u3000\t`);
    expect(answerPosts(requests)).toHaveLength(1);
    expect((await answerPosts(requests)[0].clone().json()).question).toBe(question);
    expect(screen.getByRole('heading', { name: '证据不足，本次弃答' })).toBeTruthy();
  });

  it('正常弃答显示本次问题与原因，不读取来源或伪装成技术故障', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return json(abstentionResult());
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    const result = screen.getByRole('region', { name: '本次问答结果' });
    expect(result.textContent).toContain(QUESTION);
    expect(result.textContent).toContain(ABSTENTION);
    expect(result.textContent).toContain('不代表知识库没有相关资料');
    expect(screen.getByRole('heading', { name: '证据不足，本次弃答' })).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(sourceReads(requests)).toHaveLength(0);
    expect(answerPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it.each([409, 502])('问答HTTP%i显示安全错误，不冒充正常弃答或泄露后端detail', async status => {
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return json({ detail: PRIVATE }, status);
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: '引用问答' })).toBeTruthy();
    expect(document.body.textContent).toContain('甲知识库');
    expect(screen.queryByRole('heading', { name: '证据不足，本次弃答' })).toBeNull();
    expect(sourceReads(requests)).toHaveLength(0);
    expect(answerPosts(requests)).toHaveLength(1);
    expectNoAnswerContent();
    expectNoPrivateOutput();
  });

  it('混合问答422保留固定参数/上下文/处理量提示和真实排错编号，不自动回退向量或重发', async () => {
    const traceId = 'a0e0b901-2b0d-4ae0-812a-bab2cf9e7b01';
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return json({ detail: PRIVATE }, 422, traceId);
    });
    render(<App />);
    await openAnswers();
    await chooseStrategy('hybrid');
    await submitQuestion();
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('参数');
    expect(alert.textContent).toContain('上下文');
    expect(alert.textContent).toContain('2000 个可检索文本块');
    expect(alert.textContent).toContain('100 万正文字符');
    expect(alert.textContent).toContain('不能确认是否已产生费用');
    expect(within(alert).getByText(traceId)).toBeTruthy();
    expectNoAnswerContent();
    expect(screen.queryByRole('heading', { name: '证据不足，本次弃答' })).toBeNull();
    expect(screen.queryByRole('button', { name: '导出回答与引文' })).toBeNull();
    expect(sourceReads(requests)).toHaveLength(0);
    await advance(30_000);
    expect(answerPosts(requests)).toHaveLength(1);
    expect(await answerPosts(requests)[0].clone().json())
      .toEqual({ question: QUESTION, top_k: 3, strategy: 'hybrid', rerank: false });
    expect(requests.some(request => pathOf(request).endsWith('/search'))).toBe(false);
    expect(strategyInput().value).toBe('hybrid');
    expectNoPrivateOutput();
  });

  it('后一个引用结构无效时整批拒绝，不能提前展示答案或第一个合法引文', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return json({ ...answerResult(),
        citations: [citation(0), citation(1), { ...citation(2), end_char: 0 }] });
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expectUnconfirmed();
    expect(screen.queryByText(/本次提交的问题：/)).toBeNull();
    expect(sourceReads(requests)).toHaveLength(0);
    expect(answerPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it.each(['not-found', 'wrong-parent', 'not-ready'])('第二来源%s时整批隐藏，不能泄露已核对的答案或引文', async problem => {
    const requests = installApi(request => {
      if (pathOf(request) !== DETAIL_B) return;
      if (problem === 'not-found') return json({ detail: PRIVATE }, 404);
      return json(documentRow(DOCUMENT_B, FILENAME_B, problem === 'wrong-parent' ? OTHER_KB : KB,
        problem === 'not-ready' ? 'parsed' : 'ready'));
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expectUnconfirmed();
    expect(screen.queryByText(/本次提交的问题：/)).toBeNull();
    expect(sourceReads(requests).map(pathOf)).toEqual([DETAIL_A, DETAIL_B]);
    expect(answerPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it.each([ANSWERS, DETAIL_B])('混合问答或来源401清除身份/问题/方式，重进默认向量（%s）', async endpoint => {
    const requests = installApi(request => {
      if (pathOf(request) === endpoint) return json({ detail: PRIVATE }, 401);
    });
    render(<App />);
    await openAnswers();
    await chooseStrategy('hybrid');
    await submitQuestion();
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByLabelText('问答问题')).toBeNull();
    expect(screen.queryByLabelText('问答检索方式')).toBeNull();
    for (const value of [QUESTION, '甲空间', '甲知识库']) expect(document.body.textContent).not.toContain(value);
    expectNoAnswerContent();
    expect(answerPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
    await signIn();
    await openAnswers();
    expect(strategyInput().value).toBe('vector');
    expect(questionInput().value).toBe('');
    expect(screen.queryByRole('region', { name: '本次问答结果' })).toBeNull();
    expect(answerPosts(requests)).toHaveLength(1);
  });

  it('混合提交快照不随问题/向量草稿改变，再次提交立即清旧并记录新向量方式', async () => {
    const second = deferred<Response>();
    let submissions = 0;
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS && ++submissions > 1) return second.promise;
    });
    render(<App />);
    await openAnswers();
    await chooseStrategy('hybrid');
    await submitQuestion();
    const nextQuestion = '导出条目的另一个问题';
    await fillQuestion(nextQuestion);
    await chooseStrategy('vector');
    const first = screen.getByRole('region', { name: '本次问答结果' });
    expect(first.textContent).toContain(QUESTION);
    expect(first.textContent).not.toContain(nextQuestion);
    expect(first.textContent).toContain('本次提交的检索方式：混合检索（向量＋词项）');
    expect(answerPosts(requests)).toHaveLength(1);
    await click(screen.getByRole('button', { name: '生成回答' }));
    expectNoAnswerContent();
    expect(screen.queryByRole('region', { name: '本次问答结果' })).toBeNull();
    await act(async () => { second.resolve(json(abstentionResult())); await vi.advanceTimersByTimeAsync(0); });
    const result = screen.getByRole('region', { name: '本次问答结果' });
    expect(result.textContent).toContain(nextQuestion);
    expect(result.textContent).not.toContain(QUESTION);
    expect(result.textContent).toContain('本次提交的检索方式：向量检索');
    expect(screen.getByRole('heading', { name: '证据不足，本次弃答' })).toBeTruthy();
    expect(answerPosts(requests)).toHaveLength(2);
    expect(await answerPosts(requests)[0].clone().json())
      .toEqual({ question: QUESTION, top_k: 3, strategy: 'hybrid', rerank: false });
    expect(await answerPosts(requests)[1].clone().json())
      .toEqual({ question: nextQuestion, top_k: 3, strategy: 'vector', rerank: false });
    expectNoPrivateOutput();
  });

  it('断线只提示本次结果未确认，不自动重发问答或将故障当弃答', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return Promise.reject(new TypeError(PRIVATE));
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expectUnconfirmed();
    await advance(30_000);
    expect(answerPosts(requests)).toHaveLength(1);
    expect(sourceReads(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it('十秒不结束生成等待，630秒超时中断单次请求且保持未确认', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return new Promise<Response>((_resolve, reject) => {
        request.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      });
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    await advance(10_001);
    expect(screen.getByRole('button', { name: '正在回答…' })).toBeTruthy();
    expect(answerPosts(requests)[0].signal.aborted).toBe(false);
    await advance(619_999);
    expect(answerPosts(requests)[0].signal.aborted).toBe(true);
    expectUnconfirmed();
    expect(answerPosts(requests)).toHaveLength(1);
  });

  it('停止等待混合问答取消浏览器请求，迟到成功不能改写结果或启动来源读取', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return pending.promise;
    });
    render(<App />);
    await openAnswers();
    await chooseStrategy('hybrid');
    await submitQuestion();
    await click(screen.getByRole('button', { name: '停止等待回答结果' }));
    expect(answerPosts(requests)[0].signal.aborted).toBe(true);
    expectUnconfirmed();
    await act(async () => { pending.resolve(json(answerResult())); await vi.advanceTimersByTimeAsync(0); });
    expectUnconfirmed();
    expect(sourceReads(requests)).toHaveLength(0);
    expect(answerPosts(requests)).toHaveLength(1);
    expect((await answerPosts(requests)[0].clone().json()).strategy).toBe('hybrid');
  });

  it('返回文档后可切换知识库，旧问答迟到成功不能污染新范围', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return pending.promise;
    });
    render(<App />);
    await openAnswers();
    await chooseStrategy('hybrid');
    await submitQuestion();
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(answerPosts(requests)[0].signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(requests.filter(request => pathOf(request) === DOCUMENTS)).toHaveLength(2);
    await click(screen.getByRole('button', { name: '返回知识库' }));
    await click(screen.getByRole('button', { name: '查看乙知识库的文档' }));
    await click(screen.getByRole('button', { name: '引用问答' }));
    expect(questionInput().value).toBe('');
    expect(strategyInput().value).toBe('vector');
    await act(async () => { pending.resolve(json(answerResult())); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('heading', { level: 1, name: '引用问答' })).toBeTruthy();
    expect(document.body.textContent).toContain('乙知识库');
    expect(document.body.textContent).not.toContain('甲知识库');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('region', { name: '本次问答结果' })).toBeNull();
    expectNoAnswerContent();
    expect(sourceReads(requests)).toHaveLength(0);
    expect(answerPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('来源核对期间返回也取消读取，迟到来源不能在文档页展示答案', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === DETAIL_B) return pending.promise;
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    expect(sourceReads(requests)).toHaveLength(2);
    expectNoAnswerContent();
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(sourceReads(requests)[1].signal.aborted).toBe(true);
    await act(async () => { pending.resolve(json(documentRow(DOCUMENT_B, FILENAME_B))); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: '本次问答结果' })).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expectNoAnswerContent();
    expect(answerPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('退出中断问答，退出失败保留未确认，迟到成功不能覆盖退出错误', async () => {
    const pending = deferred<Response>();
    const exiting = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === ANSWERS) return pending.promise;
      if (pathOf(request) === LOGOUT) return exiting.promise;
    });
    render(<App />);
    await openAnswers();
    await submitQuestion();
    await click(screen.getByRole('button', { name: '退出登录' }));
    expect(answerPosts(requests)[0].signal.aborted).toBe(true);
    await act(async () => {
      pending.resolve(json(answerResult()));
      exiting.reject(new TypeError(PRIVATE));
      await vi.advanceTimersByTimeAsync(0);
    });
    expectUnconfirmed();
    expect(document.body.textContent).toContain('退出尚未确认');
    expect(screen.queryByText(/本次提交的问题：/)).toBeNull();
    expect(answerPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('混合问答不续期，到期清问题和方式并隔离迟到错误，重进默认向量', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === RESTORE) return sessionResponse(Date.now() / 1000 + 60);
      if (pathOf(request) === ANSWERS) return pending.promise;
    });
    render(<App />);
    await openAnswers();
    await advance(30_000);
    await chooseStrategy('hybrid');
    await submitQuestion();
    await advance(30_000);
    expect(answerPosts(requests)[0].signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toBe('登录已到期，请重新登录。');
    expect(screen.queryByLabelText('问答问题')).toBeNull();
    expect(screen.queryByLabelText('问答检索方式')).toBeNull();
    expect(document.body.textContent).not.toContain(QUESTION);
    await act(async () => { pending.resolve(json({ detail: PRIVATE }, 404)); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('alert').textContent).toBe('登录已到期，请重新登录。');
    expect(document.body.textContent).not.toMatch(/问答结果未确认/);
    expectNoAnswerContent();
    expect(requests.filter(request => pathOf(request) === RESTORE)).toHaveLength(1);
    expectNoPrivateOutput();
    await signIn();
    await openAnswers();
    expect(strategyInput().value).toBe('vector');
    expect(questionInput().value).toBe('');
    expect(answerPosts(requests)).toHaveLength(1);
  });

  it('重新挂载只恢复会话，混合方式、所选知识库、问题与答案均不持久化或自动重发', async () => {
    const requests = installApi();
    const first = render(<App />);
    await openAnswers();
    await chooseStrategy('hybrid');
    await submitQuestion();
    expect(screen.getByRole('region', { name: '本次问答结果' })).toBeTruthy();
    first.unmount();
    render(<App />);
    await advance();
    expect(screen.getByRole('heading', { level: 1, name: '你的工作空间' })).toBeTruthy();
    expect(screen.queryByLabelText('问答问题')).toBeNull();
    expect(document.body.textContent).not.toContain(QUESTION);
    expectNoAnswerContent();
    await click(screen.getByRole('button', { name: '进入甲空间' }));
    await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
    await click(screen.getByRole('button', { name: '引用问答' }));
    expect(questionInput().value).toBe('');
    expect(strategyInput().value).toBe('vector');
    expect(screen.queryByRole('region', { name: '本次问答结果' })).toBeNull();
    expect(answerPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });
});
