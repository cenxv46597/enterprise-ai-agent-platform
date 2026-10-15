import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import App from './App';

const TOKEN = 'fake.test.token';
const PASSWORD = 'fake-ui-test-password';
const PRIVATE_RESPONSE = 'PRIVATE_RESPONSE_MUST_NOT_BE_DISPLAYED';
const USER_ID = '00000000-0000-0000-0000-000000000001';
const SPACE_ID = '00000000-0000-0000-0000-00000000000b';
const LOGIN = '/api/v1/auth/login';
const ME = '/api/v1/auth/me';
const SPACES = '/api/v1/workspaces';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function loginResponse(expiresIn = 1800): Response {
  return json({ access_token: TOKEN, token_type: 'bearer', expires_in: expiresIn });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

type Route = (request: Request) => Response | Promise<Response> | undefined;

// 模拟层故意不因 signal 自动拒绝：迟到响应测试需验证界面自身的会话保护。
function installApi(route: Route = () => undefined): Request[] {
  const requests: Request[] = [];
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request
      ? new Request(input, init)
      : new Request(new URL(String(input), 'http://localhost'), init);
    requests.push(request);
    const response = route(request);
    if (response !== undefined) return response;
    switch (new URL(request.url).pathname) {
      case LOGIN:
        return loginResponse();
      case ME:
        return json({ id: USER_ID, username: 'demo_a' });
      case SPACES:
        return json([{ id: SPACE_ID, name: '甲空间' }]);
      default:
        throw new Error('Unexpected mocked endpoint');
    }
  });
  vi.stubGlobal('fetch', fetchMock);
  return requests;
}

async function submitLogin(user = userEvent.setup()) {
  await user.type(screen.getByLabelText('用户名'), 'demo_a');
  await user.type(screen.getByLabelText('密码'), PASSWORD);
  await user.click(screen.getByRole('button', { name: '登录' }));
  return user;
}

function expectNoPrivateOutput() {
  const text = document.body.textContent ?? '';
  expect(text).not.toContain(PASSWORD);
  expect(text).not.toContain(TOKEN);
  expect(text).not.toContain(PRIVATE_RESPONSE);
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('登录与本人工作空间', () => {
  it('按登录、身份、分页列表顺序请求，并立即清空密码', async () => {
    const login = deferred<Response>();
    const spaces = deferred<Response>();
    const requests = installApi((request) => {
      const path = new URL(request.url).pathname;
      if (path === LOGIN) return login.promise;
      if (path === SPACES) return spaces.promise;
    });
    render(<App />);
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect((screen.getByLabelText('密码') as HTMLInputElement).type).toBe('password');

    await submitLogin();
    expect((screen.getByLabelText('密码') as HTMLInputElement).value).toBe('');
    expect(screen.getByRole('button', { name: /正在登录/ })).toBeTruthy();
    await act(async () => { login.resolve(loginResponse()); });
    await screen.findByRole('heading', { name: '你的工作空间' });
    expect(screen.getByText('当前账号：demo_a')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('正在读取工作空间');
    await act(async () => { spaces.resolve(json([{ id: SPACE_ID, name: '甲空间' }])); });
    await screen.findByText('甲空间');

    expect(requests.map((request) => [request.method, new URL(request.url).pathname])).toEqual([
      ['POST', LOGIN], ['GET', ME], ['GET', SPACES],
    ]);
    expect(await requests[0].json()).toEqual({ username: 'demo_a', password: PASSWORD });
    expect(requests[0].headers.has('Authorization')).toBe(false);
    expect(requests.slice(1).every((request) => request.headers.get('Authorization') === `Bearer ${TOKEN}`)).toBe(true);
    expect(new URL(requests[2].url).searchParams.toString()).toBe('limit=20&offset=0');
    expect(requests.every((request) => !request.url.includes(TOKEN) && !request.url.includes(PASSWORD))).toBe(true);
    expect(document.body.textContent).not.toContain(SPACE_ID);
    expectNoPrivateOutput();
  });

  it('登录失败只显示安全错误，不进入身份或列表请求', async () => {
    const requests = installApi((request) => {
      if (new URL(request.url).pathname === LOGIN) {
        return new Response(`${PRIVATE_RESPONSE} ${TOKEN} ${PASSWORD}`, { status: 401 });
      }
    });
    render(<App />);
    await submitLogin();
    const alert = await screen.findByRole('alert');
    expect(alert.textContent?.trim()).not.toBe('');
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(requests).toHaveLength(1);
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
      if (new URL(request.url).pathname === SPACES) return json(rows);
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

  it('列表网络错误允许显式刷新，后续401清除身份和旧空间', async () => {
    let listRequests = 0;
    installApi((request) => {
      if (new URL(request.url).pathname !== SPACES) return;
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
    expectNoPrivateOutput();
  });

  it('退出会中止列表请求，迟到的列表响应不能恢复旧会话', async () => {
    const spaces = deferred<Response>();
    const requests = installApi((request) => {
      if (new URL(request.url).pathname === SPACES) return spaces.promise;
    });
    render(<App />);
    const user = await submitLogin();
    await screen.findByRole('heading', { name: '你的工作空间' });
    const pendingRequest = requests.find((request) => new URL(request.url).pathname === SPACES)!;
    await user.click(screen.getByRole('button', { name: '退出登录' }));
    expect(pendingRequest.signal.aborted).toBe(true);
    await act(async () => { spaces.resolve(json([{ id: SPACE_ID, name: '迟到的旧空间' }])); });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByText('迟到的旧空间')).toBeNull();
    expect(screen.queryByRole('button', { name: '退出登录' })).toBeNull();
  });

  it('取消登录后迟到的成功响应不能进入工作空间页面', async () => {
    const login = deferred<Response>();
    const requests = installApi((request) => {
      if (new URL(request.url).pathname === LOGIN) return login.promise;
    });
    render(<App />);
    const user = await submitLogin();
    await user.click(screen.getByRole('button', { name: '取消登录' }));
    expect(requests[0].signal.aborted).toBe(true);
    await act(async () => { login.resolve(loginResponse()); });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();
    expect(screen.queryByText('甲空间')).toBeNull();
    expect(screen.queryByRole('heading', { name: '你的工作空间' })).toBeNull();
  });

  it('重新挂载需要重新登录，且不读写浏览器持久存储或Cookie', async () => {
    const storage = ['getItem', 'setItem', 'removeItem', 'clear'] as const;
    const storageSpies = storage.map((method) => vi.spyOn(Storage.prototype, method));
    const cookieRead = vi.spyOn(document, 'cookie', 'get');
    const cookieWrite = vi.spyOn(document, 'cookie', 'set');
    const requests = installApi();
    const mounted = render(<App />);
    await submitLogin();
    await screen.findByText('甲空间');
    mounted.unmount();
    render(<App />);
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByText('甲空间')).toBeNull();
    expect(requests).toHaveLength(3);
    for (const spy of storageSpies) expect(spy).not.toHaveBeenCalled();
    expect(cookieRead).not.toHaveBeenCalled();
    expect(cookieWrite).not.toHaveBeenCalled();
    expectNoPrivateOutput();
  });

  it('访问令牌过期时清除内存会话和已显示的空间', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
    installApi((request) => {
      if (new URL(request.url).pathname === LOGIN) return loginResponse(2);
    });
    render(<App />);
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'demo_a' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: PASSWORD } });
    await act(async () => {
      fireEvent.submit(screen.getByRole('button', { name: '登录' }).closest('form')!);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { name: '你的工作空间' })).toBeTruthy();
    expect(screen.getByText('甲空间')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(2001); });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByText('甲空间')).toBeNull();
    expect(screen.queryByText('当前账号：demo_a')).toBeNull();
    expectNoPrivateOutput();
  });
});
