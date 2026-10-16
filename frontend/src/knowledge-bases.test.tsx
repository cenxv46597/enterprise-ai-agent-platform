import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import App from './App';

const USER_ID = '00000000-0000-0000-0000-000000000001';
const SPACE_A = '00000000-0000-0000-0000-00000000000a';
const SPACE_B = '00000000-0000-0000-0000-00000000000b';
const KB_A = '00000000-0000-0000-0000-000000000101';
const KB_B = '00000000-0000-0000-0000-000000000102';
const PRIVATE_RESPONSE = 'PRIVATE_KNOWLEDGE_RESPONSE_MUST_NOT_BE_DISPLAYED';
const RESTORE = '/api/v1/auth/web/session';
const LOGIN = '/api/v1/auth/web/login';
const LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces';
const KNOWLEDGE_A = `${SPACES}/${SPACE_A}/knowledge-bases`;
const KNOWLEDGE_B = `${SPACES}/${SPACE_B}/knowledge-bases`;
const EMPTY = '该工作空间暂无知识库';

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

function pathOf(request: Request): string {
  return new URL(request.url).pathname;
}

function knowledgeRequests(requests: Request[]): Request[] {
  return requests.filter((request) => pathOf(request).endsWith('/knowledge-bases'));
}

type Route = (request: Request) => Response | Promise<Response> | undefined;

// 只模拟服务端会话响应，不实现浏览器 Cookie；已取消的请求仍可返回，用于验证隔离。
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
      default:
        throw new Error('Unexpected mocked endpoint');
    }
  }));
  return requests;
}

function expectNoPrivateOutput() {
  expect(document.body.textContent).not.toContain(PRIVATE_RESPONSE);
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('工作空间内的知识库列表', () => {
  it('显式选择空间才读取其第一页，显示空间上下文并沿用 Cookie 请求约定', async () => {
    const knowledge = deferred<Response>();
    const requests = installApi((request) => {
      if (pathOf(request) === KNOWLEDGE_A) return knowledge.promise;
    });
    render(<App />);
    const enter = await screen.findByRole('button', { name: '进入甲空间' });
    expect(screen.getByRole('button', { name: '进入乙空间' })).toBeTruthy();
    expect(requests.map(pathOf)).toEqual([RESTORE, SPACES]);

    const user = userEvent.setup();
    await user.click(enter);
    expect(screen.getByRole('heading', { level: 1, name: '知识库' })).toBeTruthy();
    expect(document.body.textContent).toContain('甲空间');
    expect(screen.getByRole('button', { name: '返回工作空间' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '刷新列表' })).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('正在读取知识库…');
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();

    const [request] = knowledgeRequests(requests);
    expect(request.method).toBe('GET');
    expect(new URL(request.url).pathname).toBe(KNOWLEDGE_A);
    expect(new URL(request.url).searchParams.toString()).toBe('limit=20&offset=0');
    expect(request.credentials).toBe('same-origin');
    expect(request.headers.get('X-EAAP-Web')).toBe('1');
    expect(request.headers.has('Authorization')).toBe(false);
    await act(async () => { knowledge.resolve(json([knowledgeBase(KB_A, '甲空间知识库')])); });
    await screen.findByText('甲空间知识库');
    expect(knowledgeRequests(requests)).toHaveLength(1);
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('button', { name: /上传|问答|详情/ })).toBeNull();
    for (const id of [USER_ID, SPACE_A, SPACE_B, KB_A]) {
      expect(document.body.textContent).not.toContain(id);
    }
  });

  it.each([0, 20])('正确展示 %i 条知识库，名称作为文字且不自动翻页', async (count) => {
    const unsafeName = '<img src=x onerror="alert(1)">制度库';
    const rows = Array.from({ length: count }, (_, index) => knowledgeBase(
      `00000000-0000-0000-0000-${(index + 257).toString(16).padStart(12, '0')}`,
      index === 0 ? unsafeName : `知识库${index + 1}`,
    ));
    const requests = installApi((request) => {
      if (pathOf(request) === KNOWLEDGE_A) return json(rows);
    });
    const { container } = render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '进入甲空间' }));

    if (count === 0) {
      await screen.findByRole('heading', { level: 2, name: EMPTY });
      expect(screen.queryByText(/当前页已满/)).toBeNull();
    } else {
      await screen.findByText(unsafeName);
      for (const row of rows) {
        expect(screen.getByText(row.name)).toBeTruthy();
        expect(document.body.textContent).not.toContain(row.id);
      }
      expect(screen.getByText(/当前页已满/)).toBeTruthy();
      expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
      expect(container.querySelector('img')).toBeNull();
    }
    expect(document.body.textContent).not.toContain(SPACE_A);
    expect(knowledgeRequests(requests)).toHaveLength(1);
    expect(new URL(knowledgeRequests(requests)[0].url).searchParams.toString()).toBe('limit=20&offset=0');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('刷新失败清除旧列表并保留当前身份，显式重试时显示加载而非空态', async () => {
    let reads = 0;
    const retry = deferred<Response>();
    const requests = installApi((request) => {
      if (pathOf(request) !== KNOWLEDGE_A) return;
      reads += 1;
      if (reads === 2) throw new TypeError(PRIVATE_RESPONSE);
      if (reads === 3) return retry.promise;
    });
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '进入甲空间' }));
    await screen.findByText('甲空间知识库');
    await user.click(screen.getByRole('button', { name: '刷新列表' }));
    await screen.findByRole('alert');
    expect(screen.getByRole('heading', { level: 1, name: '知识库' })).toBeTruthy();
    expect(screen.getByText('当前账号：demo_a')).toBeTruthy();
    expect(document.body.textContent).toContain('甲空间');
    expect(screen.queryByText('甲空间知识库')).toBeNull();
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    expectNoPrivateOutput();

    await user.click(screen.getByRole('button', { name: '重试加载' }));
    expect(screen.getByRole('status').textContent).toContain('正在读取知识库…');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    await act(async () => { retry.resolve(json([knowledgeBase(KB_A, '重试后知识库')])); });
    await screen.findByText('重试后知识库');
    expect(knowledgeRequests(requests)).toHaveLength(3);
    expect(requests.filter((request) => pathOf(request) === RESTORE)).toHaveLength(1);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it.each([401, 404])('%i 不冒充空态，按会话失效或空间不可访问处理', async (status) => {
    const requests = installApi((request) => {
      if (pathOf(request) === KNOWLEDGE_A) return json({ detail: PRIVATE_RESPONSE }, status);
    });
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '进入甲空间' }));

    if (status === 401) {
      await screen.findByRole('heading', { name: '登录工作空间' });
      expect(screen.queryByText('当前账号：demo_a')).toBeNull();
      expect(screen.queryByRole('heading', { name: '知识库' })).toBeNull();
      expect(document.body.textContent).not.toContain('甲空间');
    } else {
      const alert = await screen.findByRole('alert');
      expect(alert.textContent).toBe('该工作空间不存在或你已无权访问，请返回工作空间列表。');
      expect(screen.getByRole('heading', { level: 1, name: '知识库' })).toBeTruthy();
      expect(screen.getByText('当前账号：demo_a')).toBeTruthy();
      expect(document.body.textContent).toContain('甲空间');
      await user.click(screen.getByRole('button', { name: '返回工作空间' }));
      await screen.findByRole('button', { name: '进入甲空间' });
      expect(requests.filter((request) => pathOf(request) === SPACES)).toHaveLength(2);
    }
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    expectNoPrivateOutput();
  });

  it('返回重新读取工作空间，切换空间后忽略前一空间的迟到响应', async () => {
    const oldKnowledge = deferred<Response>();
    let spaceReads = 0;
    const requests = installApi((request) => {
      if (pathOf(request) === KNOWLEDGE_A) return oldKnowledge.promise;
      if (pathOf(request) === SPACES && ++spaceReads === 2) {
        return json([{ id: SPACE_B, name: '乙空间（已更新）' }]);
      }
    });
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '进入甲空间' }));
    const oldRequest = knowledgeRequests(requests)[0];
    await user.click(screen.getByRole('button', { name: '返回工作空间' }));
    expect(oldRequest.signal.aborted).toBe(true);
    await screen.findByRole('button', { name: '进入乙空间（已更新）' });
    expect(screen.queryByRole('button', { name: '进入甲空间' })).toBeNull();
    expect(spaceReads).toBe(2);
    await user.click(screen.getByRole('button', { name: '进入乙空间（已更新）' }));
    await screen.findByText('乙空间知识库');

    await act(async () => {
      oldKnowledge.resolve(json([knowledgeBase(KB_A, '甲空间迟到知识库')]));
    });
    expect(screen.getByText('乙空间知识库')).toBeTruthy();
    expect(document.body.textContent).toContain('乙空间（已更新）');
    expect(screen.queryByText('甲空间迟到知识库')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(knowledgeRequests(requests).map(pathOf)).toEqual([KNOWLEDGE_A, KNOWLEDGE_B]);
  });

  it('退出等待204并禁用资源按钮，隔离迟到知识库且再次登录从工作空间开始', async () => {
    const knowledge = deferred<Response>();
    const logout = deferred<Response>();
    const requests = installApi((request) => {
      if (pathOf(request) === KNOWLEDGE_A) return knowledge.promise;
      if (pathOf(request) === LOGOUT) return logout.promise;
    });
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '进入甲空间' }));
    const oldRequest = knowledgeRequests(requests)[0];
    await user.click(screen.getByRole('button', { name: '退出登录' }));
    expect(oldRequest.signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { level: 1, name: '知识库' })).toBeTruthy();
    const exit = screen.getByRole('button', { name: /正在退出/ }) as HTMLButtonElement;
    const back = screen.getByRole('button', { name: '返回工作空间' }) as HTMLButtonElement;
    const refresh = screen.getByRole('button', { name: '刷新列表' }) as HTMLButtonElement;
    expect(exit.disabled).toBe(true);
    expect(back.disabled).toBe(true);
    expect(refresh.disabled).toBe(true);
    await user.click(exit);
    await user.click(back);
    await user.click(refresh);
    expect(requests.filter((request) => pathOf(request) === LOGOUT)).toHaveLength(1);
    expect(requests.filter((request) => pathOf(request) === SPACES)).toHaveLength(1);
    expect(knowledgeRequests(requests)).toHaveLength(1);

    await act(async () => {
      knowledge.resolve(json([knowledgeBase(KB_A, '退出期间迟到的知识库')]));
    });
    expect(screen.queryByText('退出期间迟到的知识库')).toBeNull();
    await act(async () => { logout.resolve(new Response(null, { status: 204 })); });
    await screen.findByRole('heading', { name: '登录工作空间' });
    expect(document.body.textContent).not.toContain('甲空间');
    expect(screen.queryByText('当前账号：demo_a')).toBeNull();

    await user.type(screen.getByLabelText('用户名'), 'demo_a');
    await user.type(screen.getByLabelText('密码'), 'fake-knowledge-test-password');
    await user.click(screen.getByRole('button', { name: '登录' }));
    await screen.findByRole('button', { name: '进入甲空间' });
    expect(screen.getByRole('heading', { level: 1, name: '你的工作空间' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '知识库' })).toBeNull();
    expect(screen.queryByText('退出期间迟到的知识库')).toBeNull();
    expect(knowledgeRequests(requests)).toHaveLength(1);
  });

  it('进入知识库不延长会话，原到期时间清除空间与身份并隔离待完成请求', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
    const expiresAt = Date.now() / 1000 + 2;
    const knowledge = deferred<Response>();
    const requests = installApi((request) => {
      if (pathOf(request) === RESTORE) return sessionResponse(expiresAt);
      if (pathOf(request) === KNOWLEDGE_A) return knowledge.promise;
    });
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('button', { name: '进入甲空间' })).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '进入甲空间' }));
      await vi.advanceTimersByTimeAsync(0);
    });
    const oldRequest = knowledgeRequests(requests)[0];
    expect(screen.getByRole('status').textContent).toContain('正在读取知识库…');
    await act(async () => { await vi.advanceTimersByTimeAsync(999); });
    expect(screen.getByRole('heading', { level: 1, name: '知识库' })).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(oldRequest.signal.aborted).toBe(true);
    expect(screen.queryByText('当前账号：demo_a')).toBeNull();
    expect(document.body.textContent).not.toContain('甲空间');

    await act(async () => {
      knowledge.resolve(json([knowledgeBase(KB_A, '到期后迟到的知识库')]));
    });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByText('到期后迟到的知识库')).toBeNull();
    expect(screen.queryByRole('heading', { name: EMPTY })).toBeNull();
    expect(requests.filter((request) => pathOf(request) === LOGOUT)).toHaveLength(0);
    expect(knowledgeRequests(requests)).toHaveLength(1);
  });
});
