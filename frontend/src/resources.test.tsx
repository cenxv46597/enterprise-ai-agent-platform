import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const SPACE = '00000000-0000-0000-0000-000000000101';
const NEW_SPACE = '00000000-0000-0000-0000-000000000102';
const KB = '00000000-0000-0000-0000-000000000201';
const SPACES = '/api/v1/workspaces', KNOWLEDGE = `${SPACES}/${SPACE}/knowledge-bases`;
const NEW_KNOWLEDGE = `${SPACES}/${NEW_SPACE}/knowledge-bases`;
const PRIVATE = 'PRIVATE_RESPONSE_DETAIL';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});
const pathOf = (request: Request) => new URL(request.url).pathname;
function install(route: (request: Request) => Response | Promise<Response> | undefined = () => undefined) {
  const requests: Request[] = [];
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const request = new Request(new URL(String(input), 'http://localhost'), init); requests.push(request);
    const result = route(request); if (result !== undefined) return result;
    const path = pathOf(request);
    if (path.endsWith('/auth/web/session')) return json({ user: { id: SPACE, username: 'demo_user' }, expires_at: Date.now() / 1000 + 1800 });
    if (path.endsWith('/auth/web/logout')) return new Response(null, { status: 204 });
    if (path === SPACES) return json([{ id: SPACE, name: '甲空间' }]);
    if (path === KNOWLEDGE || path === NEW_KNOWLEDGE) return json([]);
    if (path.endsWith('/documents')) return json([]);
    throw new Error('Unexpected resource fixture endpoint');
  }));
  return requests;
}
async function flush() { await act(async () => { await vi.advanceTimersByTimeAsync(0); }); }
async function click(name: string) { await act(async () => { fireEvent.click(screen.getByRole('button', { name })); }); await flush(); }
async function fill(label: string, value: string) {
  await act(async () => { fireEvent.change(screen.getByLabelText(label), { target: { value } }); });
}
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2030-01-01')); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('self-service workspace and knowledge-base setup', () => {
  it('creates both containers from empty lists and enters the new library without depending on page membership', async () => {
    const requests = install(request => {
      if (pathOf(request) === SPACES) return request.method === 'POST'
        ? json({ id: NEW_SPACE, name: '新空间' }, 201) : json([]);
      if (pathOf(request) === NEW_KNOWLEDGE && request.method === 'POST')
        return json({ id: KB, workspace_id: NEW_SPACE, name: '制度库' }, 201);
    });
    render(<App />); await flush();
    await fill('新建工作空间名称', ' 新空间 '); await click('创建并进入工作空间');
    expect(screen.getByRole('heading', { name: '知识库' })).toBeTruthy();
    await fill('新建知识库名称', '制度库'); await click('创建并进入知识库');
    expect(screen.getByRole('heading', { name: '文档' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '引用问答' })).toBeTruthy();
    const posts = requests.filter(request => request.method === 'POST');
    expect(await posts[0].clone().json()).toEqual({ name: '新空间' });
    expect(await posts[1].clone().json()).toEqual({ name: '制度库' });
    expect(posts.map(pathOf)).toEqual([SPACES, NEW_KNOWLEDGE]);
  });
  it('validates locally, then keeps duplicate-name rejection separate from a valid empty list', async () => {
    const requests = install(request => pathOf(request) === KNOWLEDGE && request.method === 'POST'
      ? json({ detail: PRIVATE }, 409) : undefined);
    render(<App />); await flush(); await click('进入甲空间');
    await click('创建并进入知识库');
    expect(requests.filter(request => request.method === 'POST')).toHaveLength(0);
    await fill('新建知识库名称', '已有名称'); await click('创建并进入知识库');
    expect(screen.getByRole('alert').textContent).toContain('同名知识库');
    expect(screen.getByRole('heading', { name: '该工作空间暂无知识库' })).toBeTruthy();
    expect(document.body.textContent).not.toContain(PRIVATE);
  });
  it('unknown POST does not retry and a manual list refresh never repeats creation', async () => {
    const requests = install(request => request.method === 'POST' && pathOf(request) === SPACES
      ? Promise.reject(new TypeError(PRIVATE)) : undefined);
    render(<App />); await flush(); await fill('新建工作空间名称', '可能已创建');
    await click('创建并进入工作空间');
    expect(screen.getByRole('alert').textContent).toContain('创建结果未确认');
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    await click('刷新列表');
    expect(requests.filter(request => request.method === 'POST')).toHaveLength(1);
  });
  it('logout cancels an in-flight creation and a late 201 cannot reopen the workspace', async () => {
    let resolve!: (response: Response) => void;
    const requests = install(request => request.method === 'POST' && pathOf(request) === SPACES
      ? new Promise<Response>(done => { resolve = done; }) : undefined);
    render(<App />); await flush(); await fill('新建工作空间名称', '迟到空间');
    await click('创建并进入工作空间');
    expect((screen.getByRole('button', { name: '创建并进入工作空间' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '刷新列表' }) as HTMLButtonElement).disabled).toBe(true);
    await click('退出登录');
    await act(async () => { resolve(json({ id: NEW_SPACE, name: '迟到空间' }, 201)); }); await flush();
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(requests.some(request => pathOf(request) === NEW_KNOWLEDGE)).toBe(false);
  });
  it('creation401 resets identity without entering an unconfirmed resource', async () => {
    install(request => request.method === 'POST' ? json({ detail: PRIVATE }, 401) : undefined);
    render(<App />); await flush(); await fill('新建工作空间名称', '演示'); await click('创建并进入工作空间');
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(document.body.textContent).not.toContain('甲空间');
  });
  it.each(['workspaces', 'knowledge-bases'])('%s pages distinguish empty tails, preserve failed offsets and allow recovery', async kind => {
    const target = kind === 'workspaces' ? SPACES : KNOWLEDGE;
    let fail = true;
    const requests = install(request => {
      if (pathOf(request) !== target || request.method !== 'GET') return;
      const offset = Number(new URL(request.url).searchParams.get('offset'));
      if (offset === 20 && fail) { fail = false; return json({ detail: PRIVATE }, 503); }
      return json(offset === 40 ? [] : Array.from({ length: 20 }, (_, index) => ({
        id: `00000000-0000-0000-0000-${(offset + index + 500).toString(16).padStart(12, '0')}`,
        name: `条目${offset + index}`, ...(kind === 'knowledge-bases' ? { workspace_id: SPACE } : {}),
      })));
    });
    render(<App />); await flush();
    if (kind === 'knowledge-bases') await click('进入甲空间');
    await click('下一页');
    expect(screen.getByRole('alert')).toBeTruthy();
    await click(kind === 'workspaces' ? '刷新列表' : '重试加载');
    expect(screen.getByText('条目20')).toBeTruthy();
    await click('下一页');
    expect(screen.getByRole('heading', { name: kind === 'workspaces' ? '本页没有工作空间' : '本页没有知识库' })).toBeTruthy();
    await click('上一页'); expect(screen.getByText('条目20')).toBeTruthy();
    expect(requests.filter(request => pathOf(request) === target).map(request => new URL(request.url).searchParams.get('offset')))
      .toEqual(['0', '20', '20', '40', '20']);
  });
});
