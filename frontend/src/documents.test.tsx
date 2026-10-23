import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';

const USER_ID = '00000000-0000-0000-0000-000000000001';
const SPACE_A = '00000000-0000-0000-0000-00000000000a';
const SPACE_B = '00000000-0000-0000-0000-00000000000b';
const KB_A = '00000000-0000-0000-0000-000000000101';
const KB_A2 = '00000000-0000-0000-0000-000000000102';
const KB_B = '00000000-0000-0000-0000-000000000103';
const DOCUMENT_A = '00000000-0000-0000-0000-000000000301';
const DOCUMENT_A2 = '00000000-0000-0000-0000-000000000302';
const PRIVATE_RESPONSE = 'PRIVATE_DOCUMENT_RESPONSE_MUST_NOT_BE_DISPLAYED';
const RESTORE = '/api/v1/auth/web/session';
const LOGIN = '/api/v1/auth/web/login';
const LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces';
const KNOWLEDGE_A = `${SPACES}/${SPACE_A}/knowledge-bases`;
const KNOWLEDGE_B = `${SPACES}/${SPACE_B}/knowledge-bases`;
const DOCUMENTS_A = `${KNOWLEDGE_A}/${KB_A}/documents`;
const DOCUMENTS_A2 = `${KNOWLEDGE_A}/${KB_A2}/documents`;
const DOCUMENTS_B = `${KNOWLEDGE_B}/${KB_B}/documents`;
const EMPTY = '该知识库暂无文档';

type DocumentStatus = 'uploaded' | 'processing' | 'parsed' | 'ready' | 'failed';
type Route = (request: Request) => Response | Promise<Response> | undefined;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function sessionResponse(expiresAt = Math.floor(Date.now() / 1000) + 1800): Response {
  return json({ user: { id: USER_ID, username: 'demo_a' }, expires_at: expiresAt });
}

function knowledgeBase(id: string, name: string, workspaceId = SPACE_A) {
  return { id, name, workspace_id: workspaceId, created_at: '2026-10-16T00:00:00Z' };
}

function documentRow(
  id: string,
  originalFilename: string,
  status: DocumentStatus = 'ready',
  knowledgeBaseId = KB_A,
) {
  return {
    id,
    knowledge_base_id: knowledgeBaseId,
    original_filename: originalFilename,
    status,
    error_code: PRIVATE_RESPONSE,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function pathOf(request: Request): string {
  return new URL(request.url).pathname;
}

function documentRequests(requests: Request[]): Request[] {
  return requests.filter((request) => pathOf(request).endsWith('/documents'));
}

// 只模拟服务端会话；故意允许已取消请求返回，以验证页面不会接收迟到结果。
function installApi(route: Route = () => undefined): Request[] {
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
      case LOGIN:
        return sessionResponse();
      case LOGOUT:
        return new Response(null, { status: 204 });
      case SPACES:
        return json([{ id: SPACE_A, name: '甲空间' }, { id: SPACE_B, name: '乙空间' }]);
      case KNOWLEDGE_A:
        return json([knowledgeBase(KB_A, '甲空间知识库')]);
      case KNOWLEDGE_B:
        return json([knowledgeBase(KB_B, '乙空间知识库', SPACE_B)]);
      case DOCUMENTS_A:
        return json([documentRow(DOCUMENT_A, '初始文档.txt')]);
      case DOCUMENTS_A2:
        return json([documentRow(DOCUMENT_A2, '另一知识库文档.txt', 'ready', KB_A2)]);
      case DOCUMENTS_B:
        return json([documentRow(DOCUMENT_A2, '乙空间文档.txt', 'ready', KB_B)]);
      default:
        throw new Error('Unexpected mocked endpoint');
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

async function openDocuments() {
  await advance();
  await click(screen.getByRole('button', { name: '进入甲空间' }));
  await click(screen.getByRole('button', { name: '查看甲空间知识库的文档' }));
}

async function signIn() {
  await act(async () => {
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'demo_a' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'fake-document-test-password' } });
  });
  await click(screen.getByRole('button', { name: '登录' }));
}

function expectNoPrivateOutput() {
  expect(document.body.textContent).not.toContain(PRIVATE_RESPONSE);
}

async function filenameDraft(value: string) {
  await act(async () => { fireEvent.change(screen.getByLabelText('按文件名查找'), { target: { value } }); });
}

async function findFilename(value: string) {
  await filenameDraft(value);
  await click(screen.getByRole('button', { name: '查找' }));
}

function filenamePage(start: number, count = 20) {
  return Array.from({ length: count }, (_, index) => documentRow(
    `00000000-0000-0000-0000-${(start + index + 0x501).toString(16).padStart(12, '0')}`,
    `报销资料${start + index + 1}.txt`,
  ));
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

describe('知识库内的文档列表', () => {
  it('按空间、知识库、文档顺序显式读取，保留上下文且默认同时展示五种状态', async () => {
    const documents = deferred<Response>();
    const states: [DocumentStatus, string][] = [
      ['uploaded', '已上传'], ['processing', '解析中'], ['parsed', '已解析'],
      ['ready', '可检索'], ['failed', '解析失败'],
    ];
    const rows = states.map(([status], index) => documentRow(
      `00000000-0000-0000-0000-${(index + 0x301).toString(16).padStart(12, '0')}`,
      `${status}文档.txt`, status,
    ));
    const requests = installApi((request) => {
      if (pathOf(request) === DOCUMENTS_A) return documents.promise;
    });
    render(<App />);
    await advance();
    expect(requests.map(pathOf)).toEqual([RESTORE, SPACES]);

    await click(screen.getByRole('button', { name: '进入甲空间' }));
    expect(screen.getByRole('heading', { level: 1, name: '知识库' })).toBeTruthy();
    const view = screen.getByRole('button', { name: '查看甲空间知识库的文档' });
    expect(view.textContent).toBe('查看文档');
    expect(requests.map(pathOf)).toEqual([RESTORE, SPACES, KNOWLEDGE_A]);
    expect(documentRequests(requests)).toHaveLength(0);

    await click(view);
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 1, name: '知识库' })).toBeNull();
    expect(document.body.textContent).toContain('甲空间');
    expect(document.body.textContent).toContain('甲空间知识库');
    expect(screen.getByRole('button', { name: '返回知识库' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '刷新列表' })).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('正在读取文档…');
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();

    const [request] = documentRequests(requests);
    expect(request.method).toBe('GET');
    expect(pathOf(request)).toBe(DOCUMENTS_A);
    expect(new URL(request.url).searchParams.toString()).toBe('limit=20&offset=0');
    expect(new URL(request.url).searchParams.has('status')).toBe(false);
    expect(request.credentials).toBe('same-origin');
    expect(request.headers.get('X-EAAP-Web')).toBe('1');
    expect(request.headers.has('Authorization')).toBe(false);

    await act(async () => {
      documents.resolve(json(rows));
      await vi.advanceTimersByTimeAsync(0);
    });
    for (const [index, [, label]] of states.entries()) {
      const heading = screen.getByRole('heading', { level: 2, name: rows[index].original_filename });
      const item = heading.closest('li');
      expect(item).not.toBeNull();
      expect(within(item!).getByText(label)).toBeTruthy();
    }
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(documentRequests(requests)).toHaveLength(1);
    expect(screen.getByText('当前页 · 5 份文档 · 1 份可检索 · 全部处理状态')).toBeTruthy();
    for (const id of [USER_ID, SPACE_A, SPACE_B, KB_A, ...rows.map(row => row.id)]) {
      expect(document.body.textContent).not.toContain(id);
    }
    expectNoPrivateOutput();
  });

  it('零条文档显示专属空态并保留空间和知识库上下文', async () => {
    const requests = installApi((request) => {
      if (pathOf(request) === DOCUMENTS_A) return json([]);
    });
    render(<App />);
    await openDocuments();

    expect(screen.getByRole('heading', { level: 2, name: EMPTY })).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(document.body.textContent).toContain('甲空间知识库');
    expect(screen.getByText('当前账号：demo_a')).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText(/当前页已满/)).toBeNull();
    expect(documentRequests(requests)).toHaveLength(1);
  });

  it('展示满页二十条且不自动翻页，文件名按文字转义并隐藏原始标识', async () => {
    const unsafeFilename = '<img src=x onerror="alert(1)">制度文档.txt';
    const rows = Array.from({ length: 20 }, (_, index) => documentRow(
      `00000000-0000-0000-0000-${(index + 0x301).toString(16).padStart(12, '0')}`,
      index === 0 ? unsafeFilename : `文档${index + 1}.txt`,
    ));
    const requests = installApi((request) => {
      if (pathOf(request) === DOCUMENTS_A) return json(rows);
    });
    const { container } = render(<App />);
    await openDocuments();

    for (const row of rows) {
      expect(screen.getByRole('heading', { level: 2, name: row.original_filename })).toBeTruthy();
      expect(document.body.textContent).not.toContain(row.id);
    }
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/当前页已满/)).toBeTruthy();
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    expect(document.body.textContent).not.toContain(KB_A);
    expect(document.body.textContent).not.toContain(SPACE_A);
    expectNoPrivateOutput();
    await advance();
    expect(documentRequests(requests)).toHaveLength(1);
    expect(new URL(documentRequests(requests)[0].url).searchParams.toString()).toBe('limit=20&offset=0');
  });

  it('刷新先清旧文档，网络和结构错误保留所选库并允许显式重试', async () => {
    let documentReads = 0;
    const refresh = deferred<Response>();
    const retry = deferred<Response>();
    const requests = installApi((request) => {
      if (pathOf(request) !== DOCUMENTS_A) return;
      documentReads += 1;
      if (documentReads === 2) return refresh.promise;
      if (documentReads === 3) return retry.promise;
      if (documentReads === 4) return json([documentRow(DOCUMENT_A, '重试成功文档.txt')]);
    });
    render(<App />);
    await openDocuments();
    expect(screen.getByRole('heading', { level: 2, name: '初始文档.txt' })).toBeTruthy();

    await click(screen.getByRole('button', { name: '刷新列表' }));
    expect(screen.queryByText('初始文档.txt')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('正在读取文档…');
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    await act(async () => {
      refresh.reject(new TypeError(PRIVATE_RESPONSE));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(document.body.textContent).toContain('甲空间知识库');
    expect(screen.getByText('当前账号：demo_a')).toBeTruthy();
    expectNoPrivateOutput();

    await click(screen.getByRole('button', { name: '重试加载' }));
    expect(screen.getByRole('status').textContent).toContain('正在读取文档…');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    await act(async () => {
      retry.resolve(json([{ ...documentRow(DOCUMENT_A, PRIVATE_RESPONSE), status: 'unknown' }]));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(document.body.textContent).toContain('甲空间知识库');
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    expectNoPrivateOutput();

    await click(screen.getByRole('button', { name: '重试加载' }));
    expect(screen.getByRole('heading', { level: 2, name: '重试成功文档.txt' })).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(documentRequests(requests)).toHaveLength(4);
    expect(requests.filter(request => pathOf(request) === RESTORE)).toHaveLength(1);
    expect(requests.filter(request => pathOf(request) === KNOWLEDGE_A)).toHaveLength(1);
  });

  it('文档请求401清除身份和所有列表，再次登录从工作空间开始', async () => {
    let documentReads = 0;
    const requests = installApi((request) => {
      if (pathOf(request) === DOCUMENTS_A && ++documentReads === 2) {
        return json({ detail: PRIVATE_RESPONSE }, 401);
      }
    });
    render(<App />);
    await openDocuments();
    expect(screen.getByText('初始文档.txt')).toBeTruthy();
    await click(screen.getByRole('button', { name: '刷新列表' }));

    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByText('当前账号：demo_a')).toBeNull();
    expect(screen.queryByRole('heading', { level: 1, name: '文档' })).toBeNull();
    expect(screen.queryByRole('heading', { level: 1, name: '知识库' })).toBeNull();
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    expect(screen.queryByText('初始文档.txt')).toBeNull();
    expect(document.body.textContent).not.toContain('甲空间');
    expectNoPrivateOutput();

    await signIn();
    expect(screen.getByRole('heading', { level: 1, name: '你的工作空间' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '进入甲空间' })).toBeTruthy();
    expect(screen.queryByText('甲空间知识库')).toBeNull();
    expect(screen.queryByText('初始文档.txt')).toBeNull();
    expect(documentRequests(requests)).toHaveLength(2);
    await click(screen.getByRole('button', { name: '进入甲空间' }));
    expect(screen.getByRole('heading', { level: 1, name: '知识库' })).toBeTruthy();
    expect(requests.filter(request => pathOf(request) === KNOWLEDGE_A)).toHaveLength(2);
    expect(documentRequests(requests)).toHaveLength(2);
  });

  it('404保留知识库上下文且不冒充空态，返回时重新读取当前空间知识库', async () => {
    const requests = installApi((request) => {
      if (pathOf(request) === DOCUMENTS_A) return json({ detail: PRIVATE_RESPONSE }, 404);
    });
    render(<App />);
    await openDocuments();

    expect(screen.getByRole('alert').textContent).toBe('该知识库不存在或你已无权访问，请返回知识库列表。');
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(screen.getByText('当前账号：demo_a')).toBeTruthy();
    expect(document.body.textContent).toContain('甲空间');
    expect(document.body.textContent).toContain('甲空间知识库');
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    expectNoPrivateOutput();

    await click(screen.getByRole('button', { name: '返回知识库' }));
    expect(screen.getByRole('heading', { level: 1, name: '知识库' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '查看甲空间知识库的文档' })).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 1, name: '文档' })).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(requests.filter(request => pathOf(request) === KNOWLEDGE_A)).toHaveLength(2);
    expect(requests.filter(request => pathOf(request) === SPACES)).toHaveLength(1);
    expect(documentRequests(requests)).toHaveLength(1);
  });

  it.each([200, 404])('返回取消文档请求并重查知识库，切换库后隔离迟到响应（%i）', async (lateStatus) => {
    const oldDocuments = deferred<Response>();
    let knowledgeReads = 0;
    const requests = installApi((request) => {
      if (pathOf(request) === DOCUMENTS_A) return oldDocuments.promise;
      if (pathOf(request) === KNOWLEDGE_A && ++knowledgeReads === 2) {
        return json([knowledgeBase(KB_A2, '甲空间另一知识库（已更新）')]);
      }
    });
    render(<App />);
    await openDocuments();
    const [oldRequest] = documentRequests(requests);

    await click(screen.getByRole('button', { name: '返回知识库' }));
    expect(oldRequest.signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { level: 1, name: '知识库' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '查看甲空间另一知识库（已更新）的文档' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '查看甲空间知识库的文档' })).toBeNull();
    expect(knowledgeReads).toBe(2);
    await click(screen.getByRole('button', { name: '查看甲空间另一知识库（已更新）的文档' }));
    expect(screen.getByRole('heading', { level: 2, name: '另一知识库文档.txt' })).toBeTruthy();

    await act(async () => {
      oldDocuments.resolve(lateStatus === 200
        ? json([documentRow(DOCUMENT_A, '前一知识库迟到文档.txt')])
        : json({ detail: PRIVATE_RESPONSE }, lateStatus));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: '另一知识库文档.txt' })).toBeTruthy();
    expect(document.body.textContent).toContain('甲空间另一知识库（已更新）');
    expect(screen.queryByText('前一知识库迟到文档.txt')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    expect(documentRequests(requests).map(pathOf)).toEqual([DOCUMENTS_A, DOCUMENTS_A2]);
    expectNoPrivateOutput();
  });

  it.each([200, 404])('退出等待204时禁用返回和刷新，隔离迟到文档且重新登录不恢复所选库（%i）', async (lateStatus) => {
    const oldDocuments = deferred<Response>();
    const logout = deferred<Response>();
    const requests = installApi((request) => {
      if (pathOf(request) === DOCUMENTS_A) return oldDocuments.promise;
      if (pathOf(request) === LOGOUT) return logout.promise;
    });
    render(<App />);
    await openDocuments();
    const [oldRequest] = documentRequests(requests);
    await click(screen.getByRole('button', { name: '退出登录' }));

    expect(oldRequest.signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    const exit = screen.getByRole('button', { name: /正在退出/ }) as HTMLButtonElement;
    const back = screen.getByRole('button', { name: '返回知识库' }) as HTMLButtonElement;
    const refresh = screen.getByRole('button', { name: '刷新列表' }) as HTMLButtonElement;
    expect(exit.disabled).toBe(true);
    expect(back.disabled).toBe(true);
    expect(refresh.disabled).toBe(true);
    await click(exit);
    await click(back);
    await click(refresh);
    expect(requests.filter(request => pathOf(request) === LOGOUT)).toHaveLength(1);
    expect(requests.filter(request => pathOf(request) === KNOWLEDGE_A)).toHaveLength(1);
    expect(documentRequests(requests)).toHaveLength(1);
    await act(async () => {
      oldDocuments.resolve(lateStatus === 200
        ? json([documentRow(DOCUMENT_A, '退出期间迟到文档.txt')])
        : json({ detail: PRIVATE_RESPONSE }, lateStatus));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(screen.queryByText('退出期间迟到文档.txt')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    await act(async () => {
      logout.resolve(new Response(null, { status: 204 }));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByText('当前账号：demo_a')).toBeNull();
    expect(document.body.textContent).not.toContain('甲空间');

    await signIn();
    expect(screen.getByRole('heading', { level: 1, name: '你的工作空间' })).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 1, name: '文档' })).toBeNull();
    expect(screen.queryByText('退出期间迟到文档.txt')).toBeNull();
    expect(screen.queryByText('甲空间知识库')).toBeNull();
    expect(documentRequests(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it.each([200, 404])('进入空间和文档均不延长会话，原到期时间清除上下文并隔离迟到响应（%i）', async (lateStatus) => {
    const expiresAt = Date.now() / 1000 + 2;
    const oldDocuments = deferred<Response>();
    const requests = installApi((request) => {
      if (pathOf(request) === RESTORE) return sessionResponse(expiresAt);
      if (pathOf(request) === DOCUMENTS_A) return oldDocuments.promise;
    });
    render(<App />);
    await advance();
    await advance(500);
    await click(screen.getByRole('button', { name: '进入甲空间' }));
    await advance(500);
    await click(screen.getByRole('button', { name: '查看甲空间知识库的文档' }));
    const [oldRequest] = documentRequests(requests);
    expect(screen.getByRole('status').textContent).toContain('正在读取文档…');
    await advance(999);
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    await advance(1);

    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(oldRequest.signal.aborted).toBe(true);
    expect(screen.queryByText('当前账号：demo_a')).toBeNull();
    expect(screen.queryByRole('heading', { level: 1, name: '文档' })).toBeNull();
    expect(screen.queryByRole('heading', { level: 1, name: '知识库' })).toBeNull();
    expect(document.body.textContent).not.toContain('甲空间');
    await act(async () => {
      oldDocuments.resolve(lateStatus === 200
        ? json([documentRow(DOCUMENT_A, '会话到期后迟到文档.txt')])
        : json({ detail: PRIVATE_RESPONSE }, lateStatus));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByText('会话到期后迟到文档.txt')).toBeNull();
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    expect(requests.filter(request => pathOf(request) === LOGOUT)).toHaveLength(0);
    expect(requests.filter(request => pathOf(request) === RESTORE)).toHaveLength(1);
    expect(documentRequests(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });
});

describe('按文件名查找与文档翻页的业务闭环', () => {
  it('输入不发请求，显式提交编码后的字面条件，刷新沿用已提交条件而非草稿', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === DOCUMENTS_A && new URL(request.url).searchParams.has('filename_query')) {
        return json([documentRow(DOCUMENT_A2, '报销%_/旧资料.txt')]);
      }
    });
    render(<App />);
    await openDocuments();
    await filenameDraft('  报销%_/  ');
    expect(documentRequests(requests)).toHaveLength(1);
    await click(screen.getByRole('button', { name: '查找' }));
    const query = new URL(documentRequests(requests)[1].url).searchParams;
    expect([...query]).toEqual([['limit', '20'], ['offset', '0'], ['filename_query', '报销%_/']]);
    expect(screen.queryByRole('heading', { name: '初始文档.txt' })).toBeNull();
    expect(screen.getByRole('heading', { name: '报销%_/旧资料.txt' })).toBeTruthy();
    expect((screen.getByLabelText('按文件名查找') as HTMLInputElement).value).toBe('报销%_/');
    await filenameDraft('未提交草稿');
    await click(screen.getByRole('button', { name: '刷新列表' }));
    expect(new URL(documentRequests(requests).at(-1)!.url).searchParams.get('filename_query')).toBe('报销%_/');
    expect((screen.getByLabelText('按文件名查找') as HTMLInputElement).value).toBe('未提交草稿');
    expect(requests.every(request => request.method === 'GET')).toBe(true);
  });

  it('匹配结果按20/20/5手动翻页，上一页与首页保留文件名条件', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === DOCUMENTS_A) {
        const parameters = new URL(request.url).searchParams;
        if (parameters.has('filename_query')) {
          const offset = Number(parameters.get('offset'));
          return json(filenamePage(offset, offset === 40 ? 5 : 20));
        }
      }
    });
    render(<App />);
    await openDocuments();
    await findFilename('报销');
    expect(screen.getByRole('heading', { name: '报销资料1.txt' })).toBeTruthy();
    await click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getByRole('heading', { name: '报销资料21.txt' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '报销资料1.txt' })).toBeNull();
    await click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getByRole('heading', { name: '报销资料45.txt' })).toBeTruthy();
    expect(screen.getByText('当前页 · 5 份文档 · 5 份可检索 · 全部处理状态')).toBeTruthy();
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(true);
    await click(screen.getByRole('button', { name: '上一页' }));
    await click(screen.getByRole('button', { name: '回到首页' }));
    expect(documentRequests(requests).slice(1).map(request => new URL(request.url).searchParams.get('offset')))
      .toEqual(['0', '20', '40', '20', '0']);
    expect(documentRequests(requests).slice(1).every(request => new URL(request.url).searchParams.get('filename_query') === '报销')).toBe(true);
  });

  it('普通列表也能翻页，整40条后的空尾页仍可回退且不冒充空库', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === DOCUMENTS_A) {
        const offset = Number(new URL(request.url).searchParams.get('offset'));
        return json(offset === 40 ? [] : filenamePage(offset));
      }
    });
    render(<App />);
    await openDocuments();
    await click(screen.getByRole('button', { name: '下一页' }));
    await click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getByRole('heading', { name: '当前页没有文档' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(true);
    await click(screen.getByRole('button', { name: '上一页' }));
    expect(screen.getByRole('heading', { name: '报销资料21.txt' })).toBeTruthy();
    expect(documentRequests(requests).every(request => !new URL(request.url).searchParams.has('filename_query'))).toBe(true);
  });

  it('分页失败清旧行并保留目标页和条件，手动重试只重读该页', async () => {
    let pageTwoReads = 0;
    const waiting = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) !== DOCUMENTS_A) return;
      const query = new URL(request.url).searchParams;
      if (!query.has('filename_query')) return;
      if (query.get('offset') === '0') return json(filenamePage(0));
      if (++pageTwoReads === 1) return waiting.promise;
      return json(filenamePage(20, 3));
    });
    render(<App />);
    await openDocuments();
    await findFilename('报销');
    await click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.queryByRole('heading', { name: '报销资料1.txt' })).toBeNull();
    expect((screen.getByLabelText('按文件名查找') as HTMLInputElement).disabled).toBe(true);
    await act(async () => { waiting.resolve(json({ detail: PRIVATE_RESPONSE }, 503)); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByText(/当前页：第 2 页/)).toBeTruthy();
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(true);
    await click(screen.getByRole('button', { name: '重试加载' }));
    expect(screen.getByRole('heading', { name: '报销资料21.txt' })).toBeTruthy();
    expect(documentRequests(requests).slice(-2).map(request => new URL(request.url).searchParams.toString()))
      .toEqual(['limit=20&offset=20&filename_query=%E6%8A%A5%E9%94%80', 'limit=20&offset=20&filename_query=%E6%8A%A5%E9%94%80']);
    expectNoPrivateOutput();
  });

  it('新查询回首页，无匹配与空库区分，清除后省略参数并回首页', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === DOCUMENTS_A) {
        return json(new URL(request.url).searchParams.has('filename_query') ? [] : filenamePage(0));
      }
    });
    render(<App />);
    await openDocuments();
    await click(screen.getByRole('button', { name: '下一页' }));
    await findFilename('不存在');
    expect(new URL(documentRequests(requests).at(-1)!.url).searchParams.get('offset')).toBe('0');
    expect(screen.getByRole('heading', { name: '未找到文件名匹配的文档' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    await click(screen.getByRole('button', { name: '清除查找' }));
    expect(new URL(documentRequests(requests).at(-1)!.url).searchParams.toString()).toBe('limit=20&offset=0');
    expect((screen.getByLabelText('按文件名查找') as HTMLInputElement).value).toBe('');
    expect(screen.getByRole('heading', { name: '报销资料1.txt' })).toBeTruthy();
  });

  it('从查找结果进入处理页后返回，保留查询、页码和未提交草稿', async () => {
    const target = documentRow(DOCUMENT_A2, '报销旧资料.txt');
    const requests = installApi(request => {
      if (pathOf(request) === `${DOCUMENTS_A}/${DOCUMENT_A2}`) return json(target);
      if (pathOf(request) === `${DOCUMENTS_A}/${DOCUMENT_A2}/chunks`) return json([]);
      if (pathOf(request) === DOCUMENTS_A && new URL(request.url).searchParams.has('filename_query')) {
        return json(new URL(request.url).searchParams.get('offset') === '0' ? filenamePage(0) : [target]);
      }
    });
    render(<App />);
    await openDocuments();
    await findFilename('报销');
    await click(screen.getByRole('button', { name: '下一页' }));
    await filenameDraft('尚未提交');
    await click(screen.getByRole('button', { name: '查看报销旧资料.txt的处理详情' }));
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    const query = new URL(documentRequests(requests).at(-1)!.url).searchParams;
    expect(query.get('filename_query')).toBe('报销');
    expect(query.get('offset')).toBe('20');
    expect((screen.getByLabelText('按文件名查找') as HTMLInputElement).value).toBe('尚未提交');
    expect(screen.getByRole('heading', { name: '报销旧资料.txt' })).toBeTruthy();
  });

  it.each(['😀'.repeat(256), '报\u0000销'])('非法草稿不发请求、不改变已确认列表，清除仍可恢复（%s）', async value => {
    const requests = installApi();
    render(<App />);
    await openDocuments();
    await filenameDraft(value);
    await act(async () => { fireEvent.submit(screen.getByLabelText('按文件名查找').closest('form')!); });
    expect(documentRequests(requests)).toHaveLength(1);
    expect(screen.getByRole('heading', { name: '初始文档.txt' })).toBeTruthy();
    expect(screen.getByRole('alert')).toBeTruthy();
    await click(screen.getByRole('button', { name: '清除查找' }));
    expect(documentRequests(requests)).toHaveLength(2);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it.each([200, 401])('返回并重进知识库清除条件与页码，隔离旧查找迟到结果（%i）', async status => {
    const waiting = deferred<Response>();
    let findReads = 0;
    const requests = installApi(request => {
      if (pathOf(request) === DOCUMENTS_A && new URL(request.url).searchParams.has('filename_query')) {
        if (++findReads === 1) return waiting.promise;
      }
    });
    render(<App />);
    await openDocuments();
    await findFilename('报销');
    const old = documentRequests(requests).at(-1)!;
    await click(screen.getByRole('button', { name: '返回知识库' }));
    expect(old.signal.aborted).toBe(true);
    await click(screen.getByRole('button', { name: '查看甲空间知识库的文档' }));
    await act(async () => {
      waiting.resolve(status === 200 ? json([documentRow(DOCUMENT_A2, '迟到报销资料.txt')]) : json({}, status));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { name: '初始文档.txt' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '迟到报销资料.txt' })).toBeNull();
    expect((screen.getByLabelText('按文件名查找') as HTMLInputElement).value).toBe('');
    expect(new URL(documentRequests(requests).at(-1)!.url).searchParams.toString()).toBe('limit=20&offset=0');
  });

  it.each(['401', 'expiry', 'logout'] as const)('查找中的%s清除条件且隔离迟到结果，重新登录从空间开始', async reason => {
    const waiting = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === RESTORE && reason === 'expiry') return sessionResponse(Date.now() / 1000 + 2);
      if (pathOf(request) === DOCUMENTS_A && new URL(request.url).searchParams.has('filename_query')) return waiting.promise;
    });
    render(<App />);
    await openDocuments();
    await findFilename('报销');
    const old = documentRequests(requests).at(-1)!;
    if (reason === 'expiry') await advance(2000);
    else if (reason === 'logout') await click(screen.getByRole('button', { name: '退出登录' }));
    else await act(async () => { waiting.resolve(json({}, 401)); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    if (reason !== '401') {
      expect(old.signal.aborted).toBe(true);
      await act(async () => { waiting.resolve(json([documentRow(DOCUMENT_A2, '已退出的报销资料.txt')])); await vi.advanceTimersByTimeAsync(0); });
    }
    expect(screen.queryByLabelText('按文件名查找')).toBeNull();
    expect(screen.queryByText('已退出的报销资料.txt')).toBeNull();
    await signIn();
    expect(screen.getByRole('heading', { name: '你的工作空间' })).toBeTruthy();
    await click(screen.getByRole('button', { name: '进入甲空间' }));
    await click(screen.getByRole('button', { name: '查看甲空间知识库的文档' }));
    expect((screen.getByLabelText('按文件名查找') as HTMLInputElement).value).toBe('');
    expect(new URL(documentRequests(requests).at(-1)!.url).searchParams.toString()).toBe('limit=20&offset=0');
  });
});
