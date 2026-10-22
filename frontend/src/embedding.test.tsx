import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';

const USER = '00000000-0000-0000-0000-000000000001';
const SPACE = '00000000-0000-0000-0000-00000000000a';
const KB = '00000000-0000-0000-0000-000000000101';
const DOCUMENT_A = '00000000-0000-0000-0000-000000000301';
const DOCUMENT_B = '00000000-0000-0000-0000-000000000302';
const RESTORE = '/api/v1/auth/web/session';
const LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces';
const KNOWLEDGE = `${SPACES}/${SPACE}/knowledge-bases`;
const DOCUMENTS = `${KNOWLEDGE}/${KB}/documents`;
const DETAIL_A = `${DOCUMENTS}/${DOCUMENT_A}`;
const DETAIL_B = `${DOCUMENTS}/${DOCUMENT_B}`;
const CHUNKS_A = `${DETAIL_A}/chunks`;
const CHUNKS_B = `${DETAIL_B}/chunks`;
const EMBEDDINGS_A = `${DETAIL_A}/embeddings`;
const EMBEDDINGS_B = `${DETAIL_B}/embeddings`;
const FILENAME = '同名向量资料.md';
const PRIVATE = 'PRIVATE_EMBEDDING_DETAIL_MUST_NOT_BE_DISPLAYED';
const UNCONFIRMED = '向量化结果未确认；可能已调用模型或保存结果，未自动重试。请先刷新文档与切块核对状态，不要立即重复提交。';
const CONTENTS = ['第一段正文', '第二段正文', '第三段正文', '第四段正文', '第五段正文'];

type Status = 'uploaded' | 'processing' | 'parsed' | 'ready' | 'failed';
type Route = (request: Request) => Response | Promise<Response> | undefined;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function sessionResponse(expiresAt = Date.now() / 1000 + 1800) {
  return json({ user: { id: USER, username: 'demo_a' }, expires_at: expiresAt });
}

function documentRow(id = DOCUMENT_A, status: Status = 'parsed') {
  return { id, knowledge_base_id: KB, original_filename: FILENAME, status, error_code: PRIVATE };
}

function chunkRows(documentId = DOCUMENT_A) {
  let nextStart = 0;
  return CONTENTS.map((content, index) => {
    const start = nextStart;
    nextStart += Array.from(content).length;
    return {
      id: `00000000-0000-0000-0000-${(0x500 + index).toString(16).padStart(12, '0')}`,
      document_id: documentId, chunk_index: index + 1, content,
      start_char: start, end_char: nextStart,
    };
  });
}

function embeddingResult(documentId = DOCUMENT_A, reused = false, count = 5) {
  return {
    document_id: documentId, status: 'ready', model: 'fixture-embedding-model',
    dimensions: 1024, chunk_count: count, reused,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function pathOf(request: Request) { return new URL(request.url).pathname; }
function posts(requests: Request[]) {
  return requests.filter(request => request.method === 'POST' && pathOf(request).endsWith('/embeddings'));
}

// 全部请求仅进入本地模拟；迟到响应由产品的请求隔离处理。
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
      case KNOWLEDGE: return json([{ id: KB, workspace_id: SPACE, name: '甲知识库' }]);
      case DOCUMENTS: return json([documentRow()]);
      case DETAIL_A: return json(documentRow());
      case DETAIL_B: return json(documentRow(DOCUMENT_B));
      case CHUNKS_A: return json(chunkRows());
      case CHUNKS_B: return json(chunkRows(DOCUMENT_B));
      case EMBEDDINGS_A: return json(embeddingResult());
      case EMBEDDINGS_B: return json(embeddingResult(DOCUMENT_B));
      default: throw new Error('Unexpected mocked endpoint');
    }
  }));
  return requests;
}

async function advance(milliseconds = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
}

async function click(element: HTMLElement) {
  await act(async () => {
    fireEvent.click(element);
    await vi.advanceTimersByTimeAsync(0);
  });
}

function budget() { return screen.getByLabelText('本次新生成块数上限') as HTMLInputElement; }

async function fillBudget(value: string) {
  await act(async () => { fireEvent.change(budget(), { target: { value } }); });
}

async function openDocuments() {
  await advance();
  await click(screen.getByRole('button', { name: '进入甲空间' }));
  await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
}

async function openProcessing() {
  await openDocuments();
  await click(screen.getByRole('button', { name: `查看${FILENAME}的处理详情` }));
}

function expectNoPrivateOutput() {
  expect(document.body.textContent).not.toContain(PRIVATE);
  for (const id of [USER, SPACE, KB, DOCUMENT_A, DOCUMENT_B]) {
    expect(document.body.textContent).not.toContain(id);
  }
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

describe('显式预算的文档向量化', () => {
  it('进入页面不调用模型，预算初始空且五块不会在预算三时只处理前三块', async () => {
    const requests = installApi();
    render(<App />);
    await openProcessing();
    expect(budget().value).toBe('');
    expect(posts(requests)).toHaveLength(0);
    await click(screen.getByRole('button', { name: '生成向量' }));
    for (const value of ['0', '1.5', '1001', '3']) {
      await fillBudget(value);
      await click(screen.getByRole('button', { name: '生成向量' }));
      expect(posts(requests)).toHaveLength(0);
    }
    expect(screen.getByRole('alert').textContent)
      .toContain('当前文档共有 5 个文本块，超过本次上限 3 块');
    expectNoPrivateOutput();
  });

  it('同名文档按ID提交显式预算，等待期间禁止重复及其他写入，确认结果独立展示', async () => {
    const pending = deferred<Response>();
    let detailReads = 0;
    const requests = installApi(request => {
      if (pathOf(request) === DOCUMENTS) return json([documentRow(), documentRow(DOCUMENT_B)]);
      if (pathOf(request) === DETAIL_B) return json(documentRow(DOCUMENT_B, ++detailReads > 1 ? 'ready' : 'parsed'));
      if (pathOf(request) === EMBEDDINGS_B) return pending.promise;
    });
    render(<App />);
    await openDocuments();
    const cards = screen.getAllByRole('heading', { level: 2, name: FILENAME }).map(heading => heading.closest('li')!);
    await click(within(cards[1]).getByRole('button', { name: `查看${FILENAME}的处理详情` }));
    await fillBudget('5');
    await click(screen.getByRole('button', { name: '生成向量' }));
    const running = screen.getByRole('button', { name: '正在向量化…' }) as HTMLButtonElement;
    expect(running.disabled).toBe(true);
    expect(budget().disabled).toBe(true);
    expect(budget().value).toBe('');
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '刷新文档与切块' }) as HTMLButtonElement).disabled).toBe(true);
    await click(running);
    expect(posts(requests)).toHaveLength(1);
    const [post] = posts(requests);
    expect(pathOf(post)).toBe(EMBEDDINGS_B);
    expect(await post.clone().json()).toEqual({ max_chunks: 5 });
    expect(post.credentials).toBe('same-origin');
    expect(post.headers.get('X-EAAP-Web')).toBe('1');
    expect(post.headers.get('Content-Type')).toBe('application/json');
    expect(post.headers.has('Authorization')).toBe(false);
    await act(async () => { pending.resolve(json(embeddingResult(DOCUMENT_B))); await vi.advanceTimersByTimeAsync(0); });
    const outcome = screen.getByRole('region', { name: '本次向量化结果' });
    expect(outcome.textContent).toContain('向量结果已保存');
    expect(outcome.textContent).toContain('5');
    expect(outcome.textContent).toContain('1024');
    expect(outcome.textContent).toContain('fixture-embedding-model');
    expect(screen.getByText('可检索')).toBeTruthy();
    expect(detailReads).toBe(2);
    expect(posts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('已ready的五块可以用新生成预算一复用已有完整结果', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === DETAIL_A) return json(documentRow(DOCUMENT_A, 'ready'));
      if (pathOf(request) === EMBEDDINGS_A) return json(embeddingResult(DOCUMENT_A, true));
    });
    render(<App />);
    await openProcessing();
    await fillBudget('1');
    await click(screen.getByRole('button', { name: '生成向量' }));
    expect(screen.getByRole('region', { name: '本次向量化结果' }).textContent).toContain('复用已有结果');
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    expect(await posts(requests)[0].clone().json()).toEqual({ max_chunks: 1 });
    expectNoPrivateOutput();
  });

  it.each([4, 6])('HTTP200返回块数%i与已确认的五块不一致，不能声称保存成功', async count => {
    const requests = installApi(request => {
      if (pathOf(request) === EMBEDDINGS_A) return json(embeddingResult(DOCUMENT_A, false, count));
    });
    render(<App />);
    await openProcessing();
    await fillBudget('5');
    await click(screen.getByRole('button', { name: '生成向量' }));
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(screen.queryByText(/向量结果已保存/)).toBeNull();
    expect(screen.queryByText('可检索')).toBeNull();
    expect(posts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('向量响应已确认而随后读取详情失败，保留结果并说明最新状态尚未确认', async () => {
    let detailReads = 0;
    const requests = installApi(request => {
      if (pathOf(request) === DETAIL_A && ++detailReads > 1) return Promise.reject(new TypeError(PRIVATE));
    });
    render(<App />);
    await openProcessing();
    await fillBudget('5');
    await click(screen.getByRole('button', { name: '生成向量' }));
    expect(screen.getByRole('region', { name: '本次向量化结果' }).textContent).toContain('向量结果已保存');
    expect(screen.getByRole('alert').textContent).toContain('最新状态未确认');
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    expect(posts(requests)).toHaveLength(1);
    expect(detailReads).toBe(2);
    expectNoPrivateOutput();
  });

  it('断线不自动重试，必须重新确认详情与已有块再显式填写新预算', async () => {
    let chunkReads = 0;
    let detailReads = 0;
    let requestsSubmitted = 0;
    const requests = installApi(request => {
      if (pathOf(request) === EMBEDDINGS_A && ++requestsSubmitted === 1) return Promise.reject(new TypeError(PRIVATE));
      if (pathOf(request) === EMBEDDINGS_A) return json(embeddingResult(DOCUMENT_A, true));
      if (pathOf(request) === DETAIL_A) return json(documentRow(DOCUMENT_A, ++detailReads > 1 ? 'ready' : 'parsed'));
      if (pathOf(request) === CHUNKS_A && ++chunkReads === 2) return Promise.reject(new TypeError(PRIVATE));
    });
    render(<App />);
    await openProcessing();
    await fillBudget('5');
    await click(screen.getByRole('button', { name: '生成向量' }));
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(budget().value).toBe('');
    expect(budget().disabled).toBe(true);
    await advance(30_000);
    expect(posts(requests)).toHaveLength(1);
    await click(screen.getByRole('button', { name: '刷新文档与切块' }));
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(budget().disabled).toBe(true);
    expectNoUsableAnswerEntry();
    await click(screen.getByRole('button', { name: '刷新文档与切块' }));
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    expect(budget().value).toBe('');
    expect(budget().disabled).toBe(false);
    expect(posts(requests)).toHaveLength(1);
    await fillBudget('1');
    await click(screen.getByRole('button', { name: '生成向量' }));
    expect(posts(requests)).toHaveLength(2);
    expect(screen.getByRole('region', { name: '本次向量化结果' }).textContent).toContain('复用已有结果');
    expectNoPrivateOutput();
  });

  it('向量化等待超过普通读取的十秒仍继续，到330秒转为未确认且不重发', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === EMBEDDINGS_A) return new Promise<Response>((_resolve, reject) => {
        request.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      });
    });
    render(<App />);
    await openProcessing();
    await fillBudget('5');
    await click(screen.getByRole('button', { name: '生成向量' }));
    await advance(10_001);
    expect(screen.getByRole('button', { name: '正在向量化…' })).toBeTruthy();
    expect(posts(requests)[0].signal.aborted).toBe(false);
    await advance(319_999);
    expect(posts(requests)[0].signal.aborted).toBe(true);
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(posts(requests)).toHaveLength(1);
  });

  it('停止等待只取消浏览器请求，迟到成功不能清除未确认或重新开放提交', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === EMBEDDINGS_A) return pending.promise;
    });
    render(<App />);
    await openProcessing();
    await fillBudget('5');
    await click(screen.getByRole('button', { name: '生成向量' }));
    await click(screen.getByRole('button', { name: '停止等待向量化结果' }));
    expect(posts(requests)[0].signal.aborted).toBe(true);
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    await act(async () => { pending.resolve(json(embeddingResult())); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(screen.queryByText(/向量结果已保存/)).toBeNull();
    expect(budget().disabled).toBe(true);
    expectNoUsableAnswerEntry();
    expect(posts(requests)).toHaveLength(1);
  });

  it.each([409, 422])('HTTP%i显示安全拒绝，不回显detail也不承诺零费用，核对前不能再提交', async status => {
    const requests = installApi(request => {
      if (pathOf(request) === EMBEDDINGS_A) return json({ detail: PRIVATE }, status);
    });
    render(<App />);
    await openProcessing();
    await fillBudget('5');
    await click(screen.getByRole('button', { name: '生成向量' }));
    const rejection = screen.getByRole('alert');
    expect(rejection.textContent).toContain('不能确认是否已产生费用');
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    expect(screen.queryByText(/向量结果已保存/)).toBeNull();
    expect(rejection.textContent).not.toMatch(/零费用|未调用模型|没有调用模型/);
    expect(budget().disabled).toBe(true);
    expect(posts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('向量化401清除身份、预算与处理上下文，不能把上一会话结果带到登录页', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === EMBEDDINGS_A) return json({ detail: PRIVATE }, 401);
    });
    render(<App />);
    await openProcessing();
    await fillBudget('5');
    await click(screen.getByRole('button', { name: '生成向量' }));
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByLabelText('本次新生成块数上限')).toBeNull();
    expect(screen.queryByText(FILENAME)).toBeNull();
    expect(screen.queryByText('甲知识库')).toBeNull();
    expect(screen.queryByText(CONTENTS[0])).toBeNull();
    expect(screen.queryByRole('button', { name: ANSWER_ENTRY })).toBeNull();
    expect(posts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it.each([200, 404])('等待期间返回文档列表，隔离迟到的向量化响应%i', async lateStatus => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === EMBEDDINGS_A) return pending.promise;
    });
    render(<App />);
    await openProcessing();
    await fillBudget('5');
    await click(screen.getByRole('button', { name: '生成向量' }));
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(posts(requests)[0].signal.aborted).toBe(true);
    await act(async () => {
      pending.resolve(lateStatus === 200 ? json(embeddingResult()) : json({ detail: PRIVATE }, 404));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: '本次向量化结果' })).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(posts(requests)).toHaveLength(1);
    expect(requests.filter(request => pathOf(request) === DOCUMENTS)).toHaveLength(2);
    expectNoPrivateOutput();
  });

  it('退出中断向量化，退出失败回来仍为未确认，迟到结果不能覆盖退出故障', async () => {
    const pending = deferred<Response>();
    const exiting = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === EMBEDDINGS_A) return pending.promise;
      if (pathOf(request) === LOGOUT) return exiting.promise;
    });
    render(<App />);
    await openProcessing();
    await fillBudget('5');
    await click(screen.getByRole('button', { name: '生成向量' }));
    await click(screen.getByRole('button', { name: '退出登录' }));
    expect(posts(requests)[0].signal.aborted).toBe(true);
    await act(async () => {
      pending.resolve(json(embeddingResult()));
      exiting.reject(new TypeError(PRIVATE));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(document.body.textContent).toContain('退出尚未确认');
    expect(screen.queryByText(/向量结果已保存/)).toBeNull();
    expect(budget().value).toBe('');
    expect(budget().disabled).toBe(true);
    expectNoUsableAnswerEntry();
    expectNoPrivateOutput();
  });

  it('向量化不续期，固定登录期限到期后中断等待并隔离迟到404', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === RESTORE) return sessionResponse(Date.now() / 1000 + 60);
      if (pathOf(request) === EMBEDDINGS_A) return pending.promise;
    });
    render(<App />);
    await openProcessing();
    await advance(30_000);
    await fillBudget('5');
    await click(screen.getByRole('button', { name: '生成向量' }));
    await advance(30_000);
    expect(posts(requests)[0].signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toBe('登录已到期，请重新登录。');
    await act(async () => { pending.resolve(json({ detail: PRIVATE }, 404)); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('alert').textContent).toBe('登录已到期，请重新登录。');
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    expect(screen.queryByLabelText('本次新生成块数上限')).toBeNull();
    expect(screen.queryByRole('button', { name: ANSWER_ENTRY })).toBeNull();
    expect(requests.filter(request => pathOf(request) === RESTORE)).toHaveLength(1);
    expectNoPrivateOutput();
  });
});

const ANSWER_ENTRY = '前往当前知识库引用问答';
function answerEntry() { return screen.getByRole('button', { name: ANSWER_ENTRY }) as HTMLButtonElement; }
function expectNoUsableAnswerEntry() {
  const entry = screen.queryByRole('button', { name: ANSWER_ENTRY }) as HTMLButtonElement | null;
  if (entry) expect(entry.disabled).toBe(true);
}
function expectNoAnswerRequest(requests: Request[]) {
  expect(requests.some(request => pathOf(request).endsWith('/answers'))).toBe(false);
}
function expectCleanAnswerPage() {
  expect(screen.getByRole('heading', { level: 1, name: '引用问答' })).toBeTruthy();
  expect(document.body.textContent).toContain('当前知识库中的全部可检索文档');
  expect((screen.getByLabelText('问答问题') as HTMLTextAreaElement).value).toBe('');
  expect(screen.queryByLabelText('本次新生成块数上限')).toBeNull();
  expect(screen.queryByRole('region', { name: '本次向量化结果' })).toBeNull();
  expect(document.body.textContent).not.toContain(FILENAME);
  for (const content of CONTENTS) expect(document.body.textContent).not.toContain(content);
  expectNoPrivateOutput();
}

describe('从已确认可检索文档前往当前知识库引用问答', () => {
  it('已有ready详情和非空块即可进入，不要求再次向量化；点击仅导航且问答范围是整库', async () => {
    const requests = installApi(request => pathOf(request) === DETAIL_A ? json(documentRow(DOCUMENT_A, 'ready')) : undefined);
    render(<App />); await openProcessing();
    expect(answerEntry().disabled).toBe(false);
    await fillBudget('1');
    const count = requests.length; await click(answerEntry());
    expect(requests).toHaveLength(count); expect(posts(requests)).toHaveLength(0);
    expectNoAnswerRequest(requests); expectCleanAnswerPage();
    await advance(30_000); expect(requests).toHaveLength(count);
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    await click(screen.getByRole('button', { name: `查看${FILENAME}的处理详情` }));
    expect(budget().value).toBe('');
    expect(screen.queryByRole('region', { name: '本次向量化结果' })).toBeNull();
    expect(posts(requests)).toHaveLength(0); expectNoAnswerRequest(requests);
  });

  it('parsed阶段不能直达，向量化确认并另行GET到ready后才可导航，不自动生成回答', async () => {
    let detailReads = 0;
    const requests = installApi(request => pathOf(request) === DETAIL_A
      ? json(documentRow(DOCUMENT_A, ++detailReads === 1 ? 'parsed' : 'ready')) : undefined);
    render(<App />); await openProcessing(); expectNoUsableAnswerEntry();
    await fillBudget('5'); await click(screen.getByRole('button', { name: '生成向量' }));
    expect(screen.getByRole('region', { name: '本次向量化结果' }).textContent).toContain('向量结果已保存');
    expect(detailReads).toBe(2); expect(answerEntry().disabled).toBe(false);
    const count = requests.length; await click(answerEntry());
    expect(requests).toHaveLength(count); expect(posts(requests)).toHaveLength(1);
    expectNoAnswerRequest(requests); expectCleanAnswerPage();
  });

  it('ready详情但块记录为空时不能仅凭ready直达问答', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === DETAIL_A) return json(documentRow(DOCUMENT_A, 'ready'));
      if (pathOf(request) === CHUNKS_A) return json([]);
    });
    render(<App />); await openProcessing(); expectNoUsableAnswerEntry();
    expect(screen.getByRole('heading', { level: 1, name: '文档处理' })).toBeTruthy();
    expect(posts(requests)).toHaveLength(0); expectNoAnswerRequest(requests); expectNoPrivateOutput();
  });

  it.each(['read-failure', 'non-ready'] as const)('向量化成功后详情%s，旧confirmed卡仍不能放行导航', async latest => {
    let detailReads = 0;
    const requests = installApi(request => {
      if (pathOf(request) !== DETAIL_A) return undefined;
      if (++detailReads === 1 || latest === 'non-ready') return json(documentRow());
      return json({ detail: PRIVATE }, 503);
    });
    render(<App />); await openProcessing();
    await fillBudget('5'); await click(screen.getByRole('button', { name: '生成向量' }));
    expect(screen.getByRole('region', { name: '本次向量化结果' }).textContent).toContain('向量结果已保存');
    expect(detailReads).toBe(2); expectNoUsableAnswerEntry();
    expect(screen.getByRole('heading', { level: 1, name: '文档处理' })).toBeTruthy();
    expect(posts(requests)).toHaveLength(1); expectNoAnswerRequest(requests); expectNoPrivateOutput();
  });

  it('向量POST和成功后的详情GET等待中均不能离开处理链，详情ready确认后才恢复导航', async () => {
    const embedding = deferred<Response>(), detail = deferred<Response>(); let detailReads = 0;
    const requests = installApi(request => {
      if (pathOf(request) === DETAIL_A) return ++detailReads === 1 ? json(documentRow(DOCUMENT_A, 'ready')) : detail.promise;
      if (pathOf(request) === EMBEDDINGS_A) return embedding.promise;
    });
    render(<App />); await openProcessing(); expect(answerEntry().disabled).toBe(false);
    await fillBudget('1'); await click(screen.getByRole('button', { name: '生成向量' }));
    expectNoUsableAnswerEntry(); const waitingPost = requests.length;
    const entry = screen.queryByRole('button', { name: ANSWER_ENTRY }); if (entry) await click(entry);
    expect(requests).toHaveLength(waitingPost);
    expect(screen.getByRole('heading', { level: 1, name: '文档处理' })).toBeTruthy();
    await act(async () => { embedding.resolve(json(embeddingResult(DOCUMENT_A, true))); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('region', { name: '本次向量化结果' })).toBeTruthy(); expectNoUsableAnswerEntry();
    await act(async () => { detail.resolve(json(documentRow(DOCUMENT_A, 'ready'))); await vi.advanceTimersByTimeAsync(0); });
    expect(answerEntry().disabled).toBe(false); expect(posts(requests)).toHaveLength(1); expectNoAnswerRequest(requests);
  });

});
