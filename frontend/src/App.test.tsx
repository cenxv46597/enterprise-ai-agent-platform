import { StrictMode } from 'react';
import { act, cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import App from './App';

const PASSWORD = 'fake-ui-test-password';
const PRIVATE_RESPONSE = 'PRIVATE_RESPONSE_MUST_NOT_BE_DISPLAYED';
const USER_ID = '00000000-0000-0000-0000-000000000001';
const SPACE_ID = '00000000-0000-0000-0000-00000000000b';
const LOGIN = '/api/v1/auth/web/login';
const RESTORE = '/api/v1/auth/web/session';
const LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function sessionResponse(expiresAt = Math.floor(Date.now() / 1000) + 1800): Response {
  return json({ user: { id: USER_ID, username: 'demo_a' }, expires_at: expiresAt });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

type Route = (request: Request) => Response | Promise<Response> | undefined;

// 不模拟浏览器Cookie机制；仅控制服务端会话响应，并故意允许已取消请求迟到。
function installApi(route: Route = () => undefined): Request[] {
  const requests: Request[] = [];
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request
      ? new Request(input, init)
      : new Request(new URL(String(input), 'http://localhost'), init);
    requests.push(request);
    const response = route(request);
    if (response !== undefined) return response;
    switch (new URL(request.url).pathname) {
      case RESTORE:
        return json({ detail: 'Not authenticated' }, 401);
      case LOGIN:
        return sessionResponse();
      case LOGOUT:
        return new Response(null, { status: 204 });
      case SPACES:
        return json([{ id: SPACE_ID, name: '甲空间' }]);
      default:
        throw new Error('Unexpected mocked endpoint');
    }
  }));
  return requests;
}

function pathOf(request: Request): string {
  return new URL(request.url).pathname;
}

async function submitLogin(user = userEvent.setup()) {
  await screen.findByRole('heading', { name: '登录工作空间' });
  await user.type(screen.getByLabelText('用户名'), 'demo_a');
  await user.type(screen.getByLabelText('密码'), PASSWORD);
  await user.click(screen.getByRole('button', { name: '登录' }));
  return user;
}

function expectNoPrivateOutput() {
  const text = document.body.textContent ?? '';
  expect(text).not.toContain(PASSWORD);
  expect(text).not.toContain(PRIVATE_RESPONSE);
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Cookie登录恢复与本人工作空间', () => {
  it('启动先恢复，401正常显示登录且不查询工作空间或显示错误', async () => {
    const requests = installApi();
    render(<App />);
    expect(screen.getByRole('heading', { name: '正在恢复登录' })).toBeTruthy();
    await screen.findByRole('heading', { name: '登录工作空间' });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(requests.map(pathOf)).toEqual([RESTORE]);
  });

  it('登录后直接读取分页列表，密码立即清空且不使用Bearer或/me', async () => {
    const login = deferred<Response>();
    const spaces = deferred<Response>();
    const requests = installApi((request) => {
      if (pathOf(request) === LOGIN) return login.promise;
      if (pathOf(request) === SPACES) return spaces.promise;
    });
    render(<App />);
    await screen.findByRole('heading', { name: '登录工作空间' });
    expect((screen.getByLabelText('密码') as HTMLInputElement).type).toBe('password');
    await submitLogin();
    expect((screen.getByLabelText('密码') as HTMLInputElement).value).toBe('');
    expect(screen.getByRole('button', { name: /正在登录/ })).toBeTruthy();
    await act(async () => { login.resolve(sessionResponse()); });
    await screen.findByRole('heading', { name: '你的工作空间' });
    expect(screen.getByText('当前账号：demo_a')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('正在读取工作空间');
    await act(async () => { spaces.resolve(json([{ id: SPACE_ID, name: '甲空间' }])); });
    await screen.findByText('甲空间');
    expect(requests.map((request) => [request.method, pathOf(request)])).toEqual([
      ['GET', RESTORE], ['POST', LOGIN], ['GET', SPACES],
    ]);
    expect(await requests[1].json()).toEqual({ username: 'demo_a', password: PASSWORD });
    for (const request of requests) {
      expect(request.credentials).toBe('same-origin');
      expect(request.headers.get('X-EAAP-Web')).toBe('1');
      expect(request.headers.has('Authorization')).toBe(false);
      expect(request.url).not.toContain(PASSWORD);
    }
    expect(new URL(requests[2].url).searchParams.toString()).toBe('limit=20&offset=0');
    expect(document.body.textContent).not.toContain(SPACE_ID);
    expectNoPrivateOutput();
  });

  it('登录失败仅显示安全错误并允许核对已有Cookie会话', async () => {
    const requests = installApi((request) => {
      if (pathOf(request) === LOGIN) return new Response(`${PRIVATE_RESPONSE} ${PASSWORD}`, { status: 401 });
    });
    render(<App />);
    await submitLogin();
    await screen.findByRole('alert');
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '恢复已有登录' })).toBeTruthy();
    expect(requests.map(pathOf)).toEqual([RESTORE, LOGIN]);
    expect((screen.getByLabelText('密码') as HTMLInputElement).value).toBe('');
    expectNoPrivateOutput();
  });

  it.each([0, 20])('正常展示包含 %i 条的第一页，空间名称按文字渲染', async (count) => {
    const unsafeName = '<img src=x onerror="alert(1)">甲空间';
    const rows = Array.from({ length: count }, (_, index) => ({
      id: `00000000-0000-0000-0000-${(index + 11).toString(16).padStart(12, '0')}`,
      name: index === 0 ? unsafeName : `演示空间${index + 1}`,
    }));
    installApi((request) => {
      if (pathOf(request) === SPACES) return json(rows);
    });
    const { container } = render(<App />);
    await submitLogin();
    if (count === 0) {
      await screen.findByText('暂无可访问的工作空间');
      expect(screen.queryByText(/当前页已满/)).toBeNull();
    } else {
      await screen.findByText(unsafeName);
      for (const row of rows) expect(screen.getByText(row.name)).toBeTruthy();
      expect(screen.getByText(/当前页已满/)).toBeTruthy();
      expect(container.querySelector('img[onerror]')).toBeNull();
      expect(document.body.textContent).not.toContain(rows[0].id);
    }
    expectNoPrivateOutput();
  });

  it('列表网络错误保留身份允许刷新，后续401清除身份和旧空间', async () => {
    let listRequests = 0;
    installApi((request) => {
      if (pathOf(request) !== SPACES) return;
      listRequests += 1;
      if (listRequests === 2) throw new TypeError(PRIVATE_RESPONSE);
      if (listRequests === 3) return json({ detail: PRIVATE_RESPONSE }, 401);
    });
    render(<App />);
    const user = await submitLogin();
    await screen.findByText('甲空间');
    await user.click(screen.getByRole('button', { name: '刷新列表' }));
    await screen.findByRole('alert');
    expect(screen.queryByText('暂无可访问的工作空间')).toBeNull();
    expect(screen.getByRole('heading', { name: '你的工作空间' })).toBeTruthy();
    expect(screen.getByText('当前账号：demo_a')).toBeTruthy();
    expectNoPrivateOutput();
    await user.click(screen.getByRole('button', { name: '刷新列表' }));
    await screen.findByRole('heading', { name: '登录工作空间' });
    expect(screen.queryByText('甲空间')).toBeNull();
    expect(screen.queryByText('当前账号：demo_a')).toBeNull();
    expect(listRequests).toBe(3);
  });

  it('退出须等待204，禁止重复操作并隔离迟到列表，重挂载后按401显示登录', async () => {
    let signedIn = true;
    const spaces = deferred<Response>();
    const logout = deferred<Response>();
    const requests = installApi((request) => {
      if (pathOf(request) === RESTORE) return signedIn ? sessionResponse() : json({}, 401);
      if (pathOf(request) === SPACES) return spaces.promise;
      if (pathOf(request) === LOGOUT) return logout.promise;
    });
    const mounted = render(<App />);
    await screen.findByRole('heading', { name: '你的工作空间' });
    const user = userEvent.setup();
    const oldList = requests.find((request) => pathOf(request) === SPACES)!;
    await user.click(screen.getByRole('button', { name: '退出登录' }));
    expect(oldList.signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { name: '你的工作空间' })).toBeTruthy();
    const exiting = screen.getByRole('button', { name: /正在退出/ }) as HTMLButtonElement;
    const refreshing = screen.getByRole('button', { name: '刷新列表' }) as HTMLButtonElement;
    expect(exiting.disabled).toBe(true);
    expect(refreshing.disabled).toBe(true);
    await user.click(exiting);
    await user.click(refreshing);
    expect(requests.filter((request) => pathOf(request) === LOGOUT)).toHaveLength(1);
    await act(async () => { spaces.resolve(json([{ id: SPACE_ID, name: '迟到的旧空间' }])); });
    expect(screen.queryByText('迟到的旧空间')).toBeNull();
    await act(async () => {
      signedIn = false;
      logout.resolve(new Response(null, { status: 204 }));
    });
    await screen.findByRole('heading', { name: '登录工作空间' });
    mounted.unmount();
    render(<App />);
    await screen.findByRole('heading', { name: '登录工作空间' });
    expect(requests.filter((request) => pathOf(request) === SPACES)).toHaveLength(1);
    expect(screen.queryByText('迟到的旧空间')).toBeNull();
  });

  it('退出失败仍保留当前身份，明确提示未确认并允许再次退出', async () => {
    let exitAttempts = 0;
    const requests = installApi((request) => {
      if (pathOf(request) === RESTORE) return sessionResponse();
      if (pathOf(request) === LOGOUT && ++exitAttempts === 1) return json({ detail: PRIVATE_RESPONSE }, 503);
    });
    render(<App />);
    await screen.findByText('甲空间');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '退出登录' }));
    await screen.findByText(/退出尚未确认/);
    expect(screen.getByRole('heading', { name: '你的工作空间' })).toBeTruthy();
    expect(screen.getByText('当前账号：demo_a')).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '登录工作空间' })).toBeNull();
    expectNoPrivateOutput();
    await user.click(screen.getByRole('button', { name: '退出登录' }));
    await screen.findByRole('heading', { name: '登录工作空间' });
    expect(requests.filter((request) => pathOf(request) === LOGOUT)).toHaveLength(2);
  });

  it('取消仅停止等待，迟到登录不能恢复页面，用户可显式恢复后端会话', async () => {
    let canRestore = false;
    const login = deferred<Response>();
    const requests = installApi((request) => {
      if (pathOf(request) === RESTORE) return canRestore ? sessionResponse() : json({}, 401);
      if (pathOf(request) === LOGIN) return login.promise;
    });
    render(<App />);
    const user = await submitLogin();
    await user.click(screen.getByRole('button', { name: '取消登录' }));
    expect(requests.find((request) => pathOf(request) === LOGIN)!.signal.aborted).toBe(true);
    expect(screen.getByRole('alert').textContent).toContain('登录可能已在后端生效');
    await act(async () => {
      canRestore = true;
      login.resolve(sessionResponse());
    });
    expect(screen.queryByRole('heading', { name: '你的工作空间' })).toBeNull();
    expect(requests.filter((request) => pathOf(request) === SPACES)).toHaveLength(0);
    expect(requests.filter((request) => pathOf(request) === LOGOUT)).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '恢复已有登录' }));
    await screen.findByText('甲空间');
    expect(requests.filter((request) => pathOf(request) === RESTORE)).toHaveLength(2);
  });

  it('重新挂载通过服务端恢复，不读写浏览器存储或JavaScript Cookie', async () => {
    const storageSpies = (['getItem', 'setItem', 'removeItem', 'clear'] as const)
      .map((method) => vi.spyOn(Storage.prototype, method));
    const cookieRead = vi.spyOn(document, 'cookie', 'get');
    const cookieWrite = vi.spyOn(document, 'cookie', 'set');
    let signedIn = false;
    const requests = installApi((request) => {
      if (pathOf(request) === RESTORE) return signedIn ? sessionResponse() : json({}, 401);
      if (pathOf(request) === LOGIN) { signedIn = true; return sessionResponse(); }
    });
    const mounted = render(<App />);
    await submitLogin();
    await screen.findByText('甲空间');
    mounted.unmount();
    render(<App />);
    await screen.findByText('甲空间');
    expect(screen.getByText('当前账号：demo_a')).toBeTruthy();
    expect(requests.map(pathOf)).toEqual([RESTORE, LOGIN, SPACES, RESTORE, SPACES]);
    for (const spy of storageSpies) expect(spy).not.toHaveBeenCalled();
    expect(cookieRead).not.toHaveBeenCalled();
    expect(cookieWrite).not.toHaveBeenCalled();
    expectNoPrivateOutput();
  });

  it('恢复网络失败不冒充退出，只有显式重试才再次恢复并读取列表', async () => {
    let restores = 0;
    const requests = installApi((request) => {
      if (pathOf(request) !== RESTORE) return;
      if (++restores === 1) throw new TypeError(PRIVATE_RESPONSE);
      return sessionResponse();
    });
    render(<App />);
    await screen.findByRole('heading', { name: '暂时无法恢复登录' });
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.queryByLabelText('用户名')).toBeNull();
    expect(requests.map(pathOf)).toEqual([RESTORE]);
    expectNoPrivateOutput();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '重试恢复' }));
    await screen.findByText('甲空间');
    expect(requests.map(pathOf)).toEqual([RESTORE, RESTORE, SPACES]);
  });

  it('恢复失败后返回登录必须先确认退出，退出失败时保留恢复错误页', async () => {
    let exits = 0;
    const requests = installApi((request) => {
      if (pathOf(request) === RESTORE) throw new TypeError(PRIVATE_RESPONSE);
      if (pathOf(request) === LOGOUT && ++exits === 1) return json({ detail: PRIVATE_RESPONSE }, 503);
    });
    render(<App />);
    await screen.findByRole('heading', { name: '暂时无法恢复登录' });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '返回登录' }));
    await screen.findByText(/退出尚未确认/);
    expect(screen.getByRole('heading', { name: '暂时无法恢复登录' })).toBeTruthy();
    expect(screen.queryByLabelText('用户名')).toBeNull();
    expectNoPrivateOutput();
    await user.click(screen.getByRole('button', { name: '返回登录' }));
    await screen.findByRole('heading', { name: '登录工作空间' });
    expect(requests.map(pathOf)).toEqual([RESTORE, LOGOUT, LOGOUT]);
  });

  it('StrictMode取消的首个恢复响应不能覆盖后续已确认的未登录状态', async () => {
    let restores = 0;
    const first = deferred<Response>();
    const requests = installApi((request) => {
      if (pathOf(request) !== RESTORE) return;
      return ++restores === 1 ? first.promise : json({}, 401);
    });
    render(<StrictMode><App /></StrictMode>);
    await screen.findByRole('heading', { name: '登录工作空间' });
    expect(restores).toBe(2);
    expect(requests[0].signal.aborted).toBe(true);
    await act(async () => { first.resolve(sessionResponse()); });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(requests.filter((request) => pathOf(request) === SPACES)).toHaveLength(0);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('恢复沿用原到期时间，重挂载不会续期，过期后清页面并接受恢复401', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
    const expiresAt = Date.now() / 1000 + 2;
    const requests = installApi((request) => {
      if (pathOf(request) === RESTORE) {
        return Date.now() < expiresAt * 1000 ? sessionResponse(expiresAt) : json({}, 401);
      }
    });
    const first = render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText('甲空间')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    first.unmount();
    const second = render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText('甲空间')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1001); });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByText('甲空间')).toBeNull();
    second.unmount();
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(requests.filter((request) => pathOf(request) === SPACES)).toHaveLength(2);
    expect(requests.filter((request) => pathOf(request) === LOGOUT)).toHaveLength(0);
    expectNoPrivateOutput();
  });
});
