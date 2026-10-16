import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
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
const SEARCH = `${KNOWLEDGE}/${KB}/search`;
const FILENAME_A = '导出说明.md';
const FILENAME_B = '另一份说明.md';
const PRIVATE = 'PRIVATE_SEARCH_DETAIL_MUST_NOT_BE_DISPLAYED';
const QUERY = '一个导出条目包含什么信息？';
const EMPTY = '本次未返回候选段落';
const UNCONFIRMED = '检索结果未确认；可能已调用向量服务，未自动重试。';
const UNSAFE_START = '<img src=x onerror="alert(1)">';
const LONG_CONTENT = `${UNSAFE_START}${'🙂'.repeat(305)}未展示的原文尾部`;
const SECOND_CONTENT = '同一文档的第二个候选原文';
const THIRD_CONTENT = '另一份文档的候选原文';

type Route = (request: Request) => Response | Promise<Response> | undefined;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function sessionResponse(expiresAt = Date.now() / 1000 + 1800) {
  return json({ user: { id: USER, username: 'demo_a' }, expires_at: expiresAt });
}

function documentRow(id = DOCUMENT_A, filename = FILENAME_A, knowledgeBaseId = KB, status = 'ready') {
  return { id, knowledge_base_id: knowledgeBaseId, original_filename: filename, status, error_code: PRIVATE };
}

function hit(index = 0) {
  const content = [LONG_CONTENT, SECOND_CONTENT, THIRD_CONTENT][index];
  const start = [100, 500, 0][index];
  return {
    chunk_id: `00000000-0000-0000-0000-${(0x501 + index).toString(16).padStart(12, '0')}`,
    document_id: index === 2 ? DOCUMENT_B : DOCUMENT_A,
    document_name: index === 2 ? FILENAME_B : FILENAME_A,
    chunk_index: index === 2 ? 1 : index + 1,
    content, start_char: start, end_char: start + Array.from(content).length,
    distance: [0.1234, 0.25, 0.4567][index],
    fusion_score: null, vector_rank: index + 1, lexical_rank: null, lexical_score: null,
    retrieval_rank: null, rerank_rank: null,
  };
}

function searchResult(results = [hit(0), hit(1), hit(2)]) {
  return {
    strategy: 'vector', metric: 'cosine_distance',
    model: results.length ? 'fixture-search-embedding' : null,
    dimensions: results.length ? 1024 : null,
    results, reranking: null,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function pathOf(request: Request) { return new URL(request.url).pathname; }
function searchPosts(requests: Request[]) {
  return requests.filter(request => request.method === 'POST' && pathOf(request).endsWith('/search'));
}
function sourceReads(requests: Request[]) {
  return requests.filter(request => request.method === 'GET' && [DETAIL_A, DETAIL_B].includes(pathOf(request)));
}

// 只有模拟 HTTP；故意允许迟到响应，验证页面自身的隔离而非依赖 Mock 的取消行为。
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
      // 当前页没有 ready，不代表整个知识库没有可检索文档。
      case DOCUMENTS: return json([documentRow(DOCUMENT_A, FILENAME_A, KB, 'parsed')]);
      case OTHER_DOCUMENTS: return json([]);
      case DETAIL_A: return json(documentRow());
      case DETAIL_B: return json(documentRow(DOCUMENT_B, FILENAME_B));
      case SEARCH: return json(searchResult());
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

function queryInput() { return screen.getByLabelText('检索问题') as HTMLTextAreaElement; }

async function fillQuery(value: string) {
  await act(async () => { fireEvent.change(queryInput(), { target: { value } }); });
}

async function openDocuments() {
  await advance();
  await click(screen.getByRole('button', { name: '进入甲空间' }));
  await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
}

async function openSearch() {
  await openDocuments();
  await click(screen.getByRole('button', { name: '检索知识库' }));
}

async function submitQuery(value = QUERY) {
  await fillQuery(value);
  await click(screen.getByRole('button', { name: '检索' }));
}

function expectNoPrivateOutput() {
  expect(document.body.textContent).not.toContain(PRIVATE);
  for (const id of [USER, SPACE, KB, OTHER_KB, DOCUMENT_A, DOCUMENT_B, ...[0, 1, 2].map(index => hit(index).chunk_id)]) {
    expect(document.body.textContent).not.toContain(id);
  }
}

function expectNoCandidateContent() {
  expect(document.body.textContent).not.toContain(UNSAFE_START);
  expect(document.body.textContent).not.toContain(SECOND_CONTENT);
  expect(document.body.textContent).not.toContain(THIRD_CONTENT);
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

describe('知识库独立检索页', () => {
  it('入口不依赖当前页可检索数量，进入和编辑均不发请求，非法问题被本地拒绝', async () => {
    const requests = installApi();
    render(<App />);
    await openDocuments();
    expect(document.body.textContent).toContain('0 份可检索');
    const entry = screen.getByRole('button', { name: '检索知识库' }) as HTMLButtonElement;
    expect(entry.disabled).toBe(false);
    await click(entry);
    expect(screen.getByRole('heading', { level: 1, name: '检索知识库' })).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 1, name: '文档' })).toBeNull();
    expect(document.body.textContent).toContain('甲空间');
    expect(document.body.textContent).toContain('甲知识库');
    expect(queryInput().value).toBe('');
    expect(searchPosts(requests)).toHaveLength(0);
    for (const value of ['', ' \n\t ', '🙂'.repeat(2001)]) {
      await submitQuery(value);
      expect(screen.getByRole('alert')).toBeTruthy();
      expect(searchPosts(requests)).toHaveLength(0);
    }
    await fillQuery(QUERY);
    expect(searchPosts(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it('文档读取中、读取失败及退出中不能进入检索页', async () => {
    const documents = deferred<Response>();
    const exiting = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === DOCUMENTS) return documents.promise;
      if (pathOf(request) === LOGOUT) return exiting.promise;
    });
    render(<App />);
    await openDocuments();
    expect((screen.getByRole('button', { name: '检索知识库' }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { documents.resolve(json({ detail: PRIVATE }, 404)); await vi.advanceTimersByTimeAsync(0); });
    expect((screen.getByRole('button', { name: '检索知识库' }) as HTMLButtonElement).disabled).toBe(true);
    await click(screen.getByRole('button', { name: '退出登录' }));
    expect((screen.getByRole('button', { name: '检索知识库' }) as HTMLButtonElement).disabled).toBe(true);
    expect(searchPosts(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it('上传期间不能进入检索页或触发检索请求', async () => {
    const bytes = deferred<ArrayBuffer>();
    const file = new File(['仅供模拟'], '上传中的资料.md', { type: 'text/markdown' });
    Object.defineProperty(file, 'arrayBuffer', { value: () => bytes.promise });
    const requests = installApi();
    render(<App />);
    await openDocuments();
    await act(async () => { fireEvent.change(screen.getByLabelText('选择文档'), { target: { files: [file] } }); });
    await click(screen.getByRole('button', { name: '上传' }));
    expect(screen.getByRole('status').textContent).toContain('正在上传并解析');
    const entry = screen.getByRole('button', { name: '检索知识库' }) as HTMLButtonElement;
    expect(entry.disabled).toBe(true);
    await click(entry);
    expect(screen.queryByLabelText('检索问题')).toBeNull();
    expect(searchPosts(requests)).toHaveLength(0);
  });

  it('使用明确范围和Cookie提交一次，全部唯一来源核对完成后才显示安全且有限的原文', async () => {
    const pendingSearch = deferred<Response>();
    const secondSource = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === SEARCH) return pendingSearch.promise;
      if (pathOf(request) === DETAIL_B) return secondSource.promise;
    });
    const { container } = render(<App />);
    await openSearch();
    await submitQuery(`  ${QUERY}\n`);
    const running = screen.getByRole('button', { name: '正在检索…' }) as HTMLButtonElement;
    expect(running.disabled).toBe(true);
    expect(queryInput().disabled).toBe(true);
    await click(running);
    expect(searchPosts(requests)).toHaveLength(1);
    const [post] = searchPosts(requests);
    expect(pathOf(post)).toBe(SEARCH);
    expect(await post.clone().json()).toEqual({ query: QUERY, top_k: 3, strategy: 'vector', rerank: false });
    expect(post.credentials).toBe('same-origin');
    expect(post.headers.get('X-EAAP-Web')).toBe('1');
    expect(post.headers.get('Content-Type')).toBe('application/json');
    expect(post.headers.has('Authorization')).toBe(false);
    expect(requests.some(request => /\/answers(?:\/|$)/.test(pathOf(request)))).toBe(false);
    await act(async () => { pendingSearch.resolve(json(searchResult())); await vi.advanceTimersByTimeAsync(0); });
    expect(sourceReads(requests).map(pathOf)).toEqual([DETAIL_A, DETAIL_B]);
    expectNoCandidateContent();
    expect(screen.queryByRole('region', { name: '本次检索结果' })).toBeNull();
    await act(async () => { secondSource.resolve(json(documentRow(DOCUMENT_B, FILENAME_B))); await vi.advanceTimersByTimeAsync(0); });
    const result = screen.getByRole('region', { name: '本次检索结果' });
    expect(result.textContent).toContain(QUERY);
    expect(result.textContent).toContain(FILENAME_A);
    expect(result.textContent).toContain(FILENAME_B);
    expect(result.textContent).toContain(Array.from(LONG_CONTENT).slice(0, 300).join(''));
    expect(result.textContent).not.toContain('未展示的原文尾部');
    expect(result.textContent).toContain(SECOND_CONTENT);
    expect(result.textContent).toContain(THIRD_CONTENT);
    for (const distance of ['0.1234', '0.2500', '0.4567']) expect(result.textContent).toContain(distance);
    expect(result.textContent).toMatch(/\[100,\s*\d+\)/);
    expect(container.querySelector('img')).toBeNull();
    for (const request of sourceReads(requests)) {
      expect(request.credentials).toBe('same-origin');
      expect(request.headers.get('X-EAAP-Web')).toBe('1');
      expect(request.headers.has('Authorization')).toBe(false);
    }
    expect(searchPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('2000个Unicode字符的问题合法且首尾空白不计入请求正文', async () => {
    const query = '🙂'.repeat(2000);
    const requests = installApi(request => {
      if (pathOf(request) === SEARCH) return json(searchResult([]));
    });
    render(<App />);
    await openSearch();
    await submitQuery(` \n${query} \t`);
    expect(searchPosts(requests)).toHaveLength(1);
    expect((await searchPosts(requests)[0].clone().json()).query).toBe(query);
    expect(screen.getByText(EMPTY)).toBeTruthy();
  });

  it('空候选是本次检索结果，不冒充知识库无文档或零费用，也不请求来源', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === SEARCH) return json(searchResult([]));
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    const result = screen.getByRole('region', { name: '本次检索结果' });
    expect(result.textContent).toContain(QUERY);
    expect(result.textContent).toContain(EMPTY);
    expect(result.textContent).toContain('这不代表知识库没有文档');
    expect(result.textContent).toContain('不能据此确认本次没有产生费用');
    expect(screen.queryByRole('heading', { name: '该知识库暂无文档' })).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(sourceReads(requests)).toHaveLength(0);
    expect(searchPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it.each([404, 409])('检索HTTP%i保留范围并安全报错，不冒充空候选或输出后端detail', async status => {
    const requests = installApi(request => {
      if (pathOf(request) === SEARCH) return json({ detail: PRIVATE }, status);
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    expect(screen.getByRole('alert')).toBeTruthy();
    if (status === 409) expect(screen.getByRole('alert').textContent).toContain('配置');
    expect(screen.getByRole('heading', { level: 1, name: '检索知识库' })).toBeTruthy();
    expect(document.body.textContent).toContain('甲知识库');
    expect(screen.queryByText(EMPTY)).toBeNull();
    expectNoCandidateContent();
    expect(searchPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('后一候选结构无效时拒绝整批，不能先渲染第一条原文', async () => {
    const invalidHit = { ...hit(2), end_char: 0 };
    const requests = installApi(request => {
      if (pathOf(request) === SEARCH) return json(searchResult([hit(0), hit(1), invalidHit]));
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expectNoCandidateContent();
    expect(screen.queryByText(/本次提交的问题：/)).toBeNull();
    expect(searchPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it.each(['not-found', 'wrong-parent', 'not-ready'])('第二来源%s时整批不展示，不泄露已核对的第一来源正文', async problem => {
    const requests = installApi(request => {
      if (pathOf(request) !== DETAIL_B) return;
      if (problem === 'not-found') return json({ detail: PRIVATE }, 404);
      return json(documentRow(DOCUMENT_B, FILENAME_B, problem === 'wrong-parent' ? OTHER_KB : KB,
        problem === 'not-ready' ? 'parsed' : 'ready'));
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expectNoCandidateContent();
    expect(screen.queryByText(/本次提交的问题：/)).toBeNull();
    expect(sourceReads(requests).map(pathOf)).toEqual([DETAIL_A, DETAIL_B]);
    expect(searchPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it.each([SEARCH, DETAIL_B])('检索或来源核对401清除身份、问题及知识库上下文（%s）', async endpoint => {
    const requests = installApi(request => {
      if (pathOf(request) === endpoint) return json({ detail: PRIVATE }, 401);
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByLabelText('检索问题')).toBeNull();
    expect(document.body.textContent).not.toContain(QUERY);
    expect(document.body.textContent).not.toContain('甲空间');
    expect(document.body.textContent).not.toContain('甲知识库');
    expectNoCandidateContent();
    expect(searchPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('结果摘要绑定已提交问题，编辑不篡改旧摘要，第二次提交立即清空旧候选', async () => {
    const second = deferred<Response>();
    let submissions = 0;
    const requests = installApi(request => {
      if (pathOf(request) === SEARCH && ++submissions > 1) return second.promise;
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    const nextQuery = '修改后的另一个检索问题';
    await fillQuery(nextQuery);
    expect(screen.getByRole('region', { name: '本次检索结果' }).textContent).toContain(QUERY);
    expect(screen.getByRole('region', { name: '本次检索结果' }).textContent).not.toContain(nextQuery);
    expect(searchPosts(requests)).toHaveLength(1);
    await click(screen.getByRole('button', { name: '检索' }));
    expectNoCandidateContent();
    expect(screen.queryByRole('region', { name: '本次检索结果' })).toBeNull();
    await act(async () => { second.resolve(json(searchResult([]))); await vi.advanceTimersByTimeAsync(0); });
    const result = screen.getByRole('region', { name: '本次检索结果' });
    expect(result.textContent).toContain(nextQuery);
    expect(result.textContent).not.toContain(QUERY);
    expect(result.textContent).toContain(EMPTY);
    expect(searchPosts(requests)).toHaveLength(2);
    expectNoPrivateOutput();
  });

  it('断线或读取失败不自动重试检索，也不声称未调用向量服务', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === SEARCH) return Promise.reject(new TypeError(PRIVATE));
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    expect(screen.getByRole('alert').textContent).toContain(UNCONFIRMED);
    await advance(30_000);
    expect(searchPosts(requests)).toHaveLength(1);
    expect(screen.queryByText(EMPTY)).toBeNull();
    expectNoCandidateContent();
    expectNoPrivateOutput();
  });

  it('等待超过十秒仍继续，330秒超时仅中断一次请求并显示未确认', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === SEARCH) return new Promise<Response>((_resolve, reject) => {
        request.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      });
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    await advance(10_001);
    expect(screen.getByRole('button', { name: '正在检索…' })).toBeTruthy();
    expect(searchPosts(requests)[0].signal.aborted).toBe(false);
    await advance(319_999);
    expect(searchPosts(requests)[0].signal.aborted).toBe(true);
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(searchPosts(requests)).toHaveLength(1);
    expectNoCandidateContent();
  });

  it('停止等待后不重发，迟到成功不能替换未确认提示或显示候选', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === SEARCH) return pending.promise;
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    await click(screen.getByRole('button', { name: '停止等待检索结果' }));
    expect(searchPosts(requests)[0].signal.aborted).toBe(true);
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    await act(async () => { pending.resolve(json(searchResult())); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expectNoCandidateContent();
    expect(sourceReads(requests)).toHaveLength(0);
    expect(searchPosts(requests)).toHaveLength(1);
  });

  it.each([200, 404])('返回重新读取文档且可切换知识库，迟到检索HTTP%i不能污染新范围', async lateStatus => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === SEARCH) return pending.promise;
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(searchPosts(requests)[0].signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(requests.filter(request => pathOf(request) === DOCUMENTS)).toHaveLength(2);
    await click(screen.getByRole('button', { name: '返回知识库' }));
    await click(screen.getByRole('button', { name: '查看乙知识库的文档' }));
    await click(screen.getByRole('button', { name: '检索知识库' }));
    expect(queryInput().value).toBe('');
    await act(async () => {
      pending.resolve(lateStatus === 200 ? json(searchResult()) : json({ detail: PRIVATE }, 404));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { level: 1, name: '检索知识库' })).toBeTruthy();
    expect(document.body.textContent).toContain('乙知识库');
    expect(document.body.textContent).not.toContain('甲知识库');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('region', { name: '本次检索结果' })).toBeNull();
    expectNoCandidateContent();
    expect(searchPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('来源核对期间返回也取消读取，迟到来源不能在文档列表渲染候选', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === DETAIL_B) return pending.promise;
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    expect(sourceReads(requests)).toHaveLength(2);
    expectNoCandidateContent();
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(sourceReads(requests)[1].signal.aborted).toBe(true);
    await act(async () => { pending.resolve(json(documentRow(DOCUMENT_B, FILENAME_B))); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: '本次检索结果' })).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expectNoCandidateContent();
    expect(searchPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('退出中断检索，退出失败仍提示检索未确认，迟到成功不能覆盖退出错误', async () => {
    const pending = deferred<Response>();
    const exiting = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === SEARCH) return pending.promise;
      if (pathOf(request) === LOGOUT) return exiting.promise;
    });
    render(<App />);
    await openSearch();
    await submitQuery();
    await click(screen.getByRole('button', { name: '退出登录' }));
    expect(searchPosts(requests)[0].signal.aborted).toBe(true);
    await act(async () => {
      pending.resolve(json(searchResult()));
      exiting.reject(new TypeError(PRIVATE));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(document.body.textContent).toContain(UNCONFIRMED);
    expect(document.body.textContent).toContain('退出尚未确认');
    expect(screen.queryByText(/本次提交的问题：/)).toBeNull();
    expectNoCandidateContent();
    expect(searchPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('检索不续期，会话到期清空问题并隔离迟到404', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === RESTORE) return sessionResponse(Date.now() / 1000 + 60);
      if (pathOf(request) === SEARCH) return pending.promise;
    });
    render(<App />);
    await openSearch();
    await advance(30_000);
    await submitQuery();
    await advance(30_000);
    expect(searchPosts(requests)[0].signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toBe('登录已到期，请重新登录。');
    expect(screen.queryByLabelText('检索问题')).toBeNull();
    expect(document.body.textContent).not.toContain(QUERY);
    await act(async () => { pending.resolve(json({ detail: PRIVATE }, 404)); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('alert').textContent).toBe('登录已到期，请重新登录。');
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    expectNoCandidateContent();
    expect(requests.filter(request => pathOf(request) === RESTORE)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('模拟页面重新挂载只恢复会话，问题、候选和所选知识库不持久化', async () => {
    const requests = installApi();
    const first = render(<App />);
    await openSearch();
    await submitQuery();
    expect(screen.getByRole('region', { name: '本次检索结果' })).toBeTruthy();
    first.unmount();
    render(<App />);
    await advance();
    expect(screen.getByRole('heading', { level: 1, name: '你的工作空间' })).toBeTruthy();
    expect(screen.queryByLabelText('检索问题')).toBeNull();
    expect(document.body.textContent).not.toContain(QUERY);
    expectNoCandidateContent();
    await click(screen.getByRole('button', { name: '进入甲空间' }));
    await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
    await click(screen.getByRole('button', { name: '检索知识库' }));
    expect(queryInput().value).toBe('');
    expect(screen.queryByRole('region', { name: '本次检索结果' })).toBeNull();
    expect(searchPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });
});
