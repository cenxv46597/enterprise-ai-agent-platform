import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, listKnowledgeBases, listWorkspaces, login, logout, restoreSession } from './api';

const PASSWORD = ' private-web-test-password ';
const PRIVATE = 'PRIVATE-RESPONSE-DETAIL';
const USER_ID = '00000000-0000-0000-0000-000000000001';
const WORKSPACE_ID = '00000000-0000-0000-0000-000000000011';
const NOW = 1_900_000_000_000;
const USER = { id: USER_ID, username: 'demo_a' };
const SESSION = { user: USER, expires_at: NOW / 1000 + 1800 };
const LOGIN = '/api/v1/auth/web/login';
const RESTORE = '/api/v1/auth/web/session';
const LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces?limit=20&offset=0';
const KB_WORKSPACE_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const KNOWLEDGE_BASE_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const KNOWLEDGE_BASES = `/api/v1/workspaces/${KB_WORKSPACE_ID}/knowledge-bases?limit=20&offset=0`;
const KNOWLEDGE_BASE = { id: KNOWLEDGE_BASE_ID, workspace_id: KB_WORKSPACE_ID, name: '研发知识库' };
const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
}

async function expectSafeFailure(action: Promise<unknown>, message: string, status: number | null): Promise<void> {
  const error: unknown = await action.then(() => {
    throw new Error('Expected API rejection');
  }, (failure: unknown) => failure);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({ message, status });
  for (const secret of [PASSWORD, PRIVATE]) expect(String(error)).not.toContain(secret);
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  vi.spyOn(Date, 'now').mockReturnValue(NOW);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('same-origin Cookie API boundary', () => {
  it('uses Cookie credentials and the web header without exposing a token or asking for /me', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(SESSION))
      .mockResolvedValueOnce(jsonResponse([{ id: WORKSPACE_ID, name: '研发空间', owner: 'not-returned' }]))
      .mockResolvedValueOnce(jsonResponse(SESSION))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const signal = new AbortController().signal;
    const session = await login(' DEMO_A ', PASSWORD, signal);
    expect(session).toEqual({ user: USER, expiresAt: NOW + 1_800_000 });
    expect('token' in session).toBe(false);
    expect(await listWorkspaces(signal)).toEqual([{ id: WORKSPACE_ID, name: '研发空间' }]);
    expect(await restoreSession(signal)).toEqual(session);
    await expect(logout(signal)).resolves.toBeUndefined();
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([LOGIN, SPACES, RESTORE, LOGOUT]);
    expect(fetchMock.mock.calls.map(([, options]) => options?.method)).toEqual(['POST', 'GET', 'GET', 'POST']);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ username: 'demo_a', password: PASSWORD });
    expect(fetchMock.mock.calls[3][1]?.body).toBeUndefined();
    for (const [path, options] of fetchMock.mock.calls) {
      expect(options).toMatchObject({ mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store' });
      expect(options?.signal).toBeInstanceOf(AbortSignal);
      const headers = new Headers(options?.headers);
      expect(headers.get('X-EAAP-Web')).toBe('1');
      expect(headers.has('Authorization')).toBe(false);
      expect(headers.has('Cookie')).toBe(false);
      expect(String(path)).not.toContain(PASSWORD);
    }
  });

  it('rejects invalid login fields before making any request, counting password code points', async () => {
    for (const [username, password] of [
      ['bad name', PASSWORD], ['demo_a', ''], ['demo_a', 'x'.repeat(129)], ['demo_a', '😀'.repeat(129)],
    ] as const) {
      await expectSafeFailure(login(username, password, new AbortController().signal), '用户名或密码格式不正确。', null);
    }
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(jsonResponse(SESSION));
    await login('demo_a', '😀'.repeat(128), new AbortController().signal);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body)).password).toBe('😀'.repeat(128));
  });

  it('rejects invalid identities and absent, expired, overlong or non-integer expiration data', async () => {
    const invalid = [
      {}, { ...SESSION, user: { ...USER, id: PRIVATE } },
      { ...SESSION, user: { ...USER, username: 'someone_else' } },
      { ...SESSION, expires_at: NOW / 1000 }, { ...SESSION, expires_at: NOW / 1000 - 1 },
      { ...SESSION, expires_at: NOW / 1000 + 1801 }, { ...SESSION, expires_at: true },
      { ...SESSION, expires_at: NOW / 1000 + 0.5 },
    ];
    for (const payload of invalid) {
      fetchMock.mockReset().mockResolvedValueOnce(jsonResponse(payload));
      await expectSafeFailure(login('demo_a', PASSWORD, new AbortController().signal), '后端返回的数据无效，请稍后重试。', null);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    }
  });

  it('restores the server expiration without starting another thirty minutes', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW + 1_200_000);
    fetchMock.mockResolvedValueOnce(jsonResponse(SESSION));
    expect(await restoreSession(new AbortController().signal)).toEqual({ user: USER, expiresAt: NOW + 1_800_000 });
    expect(fetchMock.mock.calls[0][0]).toBe(RESTORE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('treats restore401 as an absent session without reading private response details', async () => {
    const response = jsonResponse({ detail: PRIVATE }, 401);
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    expect(await restoreSession(new AbortController().signal)).toBeNull();
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('requires logout204 and does not parse its empty body', async () => {
    const response = new Response(null, { status: 204 });
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    await expect(logout(new AbortController().signal)).resolves.toBeUndefined();
    expect(read).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(jsonResponse({ message: PRIVATE }, 200));
    await expectSafeFailure(logout(new AbortController().signal), '后端返回的数据无效，请稍后重试。', null);
  });

  it.each([
    ['login', 401, '用户名或密码不正确。'],
    ['workspaces', 401, '登录已失效，请重新登录。'],
    ['workspaces', 503, '请求未完成，请稍后重试。'],
    ['logout', 503, '请求未完成，请稍后重试。'],
  ] as const)('reports fixed %s HTTP %s errors without reading server details', async (stage, status, message) => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: `${PRIVATE} ${PASSWORD}` }, status));
    const signal = new AbortController().signal;
    const action = stage === 'login' ? login('demo_a', PASSWORD, signal)
      : stage === 'logout' ? logout(signal) : listWorkspaces(signal);
    await expectSafeFailure(action, message, status);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('contains malformed JSON and network errors, without turning restore faults into logged-out success', async () => {
    fetchMock.mockResolvedValueOnce(new Response(`${PRIVATE} ${PASSWORD}`));
    await expectSafeFailure(listWorkspaces(new AbortController().signal), '后端返回的数据无效，请稍后重试。', null);
    fetchMock.mockRejectedValueOnce(new Error(`${PRIVATE} ${PASSWORD}`));
    await expectSafeFailure(restoreSession(new AbortController().signal), '无法连接后端，请检查服务后重试。', null);
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...SESSION, expires_at: NOW / 1000 }));
    await expectSafeFailure(restoreSession(new AbortController().signal), '后端返回的数据无效，请稍后重试。', null);
  });

  it('rejects malformed or duplicate workspace rows without returning a partial list', async () => {
    for (const payload of [
      [{ id: PRIVATE, name: '空间' }], [{ id: WORKSPACE_ID, name: '' }],
      [{ id: WORKSPACE_ID, name: '空间一' }, { id: WORKSPACE_ID, name: '空间二' }],
      Array.from({ length: 21 }, () => ({ id: WORKSPACE_ID, name: '空间' })),
    ]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      await expectSafeFailure(listWorkspaces(new AbortController().signal), '后端返回的数据无效，请稍后重试。', null);
    }
  });

  it('accepts empty and full first pages without inventing totals or roles', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    expect(await listWorkspaces(new AbortController().signal)).toEqual([]);
    const page = Array.from({ length: 20 }, (_, index) => ({
      id: `00000000-0000-0000-0000-${String(index + 10).padStart(12, '0')}`, name: `空间${index}`,
    }));
    fetchMock.mockResolvedValueOnce(jsonResponse(page));
    expect(await listWorkspaces(new AbortController().signal)).toEqual(page);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('propagates cancellation without automatically logging out or retrying', async () => {
    fetchMock.mockImplementationOnce((_path, options) => new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
    }));
    const parent = new AbortController();
    const rejection = expectSafeFailure(login('demo_a', PASSWORD, parent.signal), '请求已取消。', null);
    parent.abort(PRIVATE);
    await rejection;
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    await expectSafeFailure(login('demo_a', PASSWORD, parent.signal), '请求已取消。', null);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('aborts a request at ten seconds and cleans its timer', async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementationOnce((_path, options) => new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
    }));
    const rejection = expectSafeFailure(listWorkspaces(new AbortController().signal), '请求超时，请稍后重试。', null);
    await vi.advanceTimersByTimeAsync(10_000);
    await rejection;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('knowledge-base first-page API boundary', () => {
  it('normalizes UUIDs and requests the selected workspace using the same-origin Cookie boundary', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([{
      ...KNOWLEDGE_BASE,
      id: KNOWLEDGE_BASE_ID.toUpperCase(),
      workspace_id: KB_WORKSPACE_ID.toUpperCase(),
      created_at: PRIVATE,
      owner: PRIVATE,
    }]));
    const signal = new AbortController().signal;
    expect(await listKnowledgeBases(KB_WORKSPACE_ID.toUpperCase(), signal)).toEqual([{
      id: KNOWLEDGE_BASE_ID, workspaceId: KB_WORKSPACE_ID, name: '研发知识库',
    }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe(KNOWLEDGE_BASES);
    expect(options).toMatchObject({
      method: 'GET', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
    });
    expect(options?.body).toBeUndefined();
    expect(options?.signal).toBeInstanceOf(AbortSignal);
    const headers = new Headers(options?.headers);
    expect(headers.get('X-EAAP-Web')).toBe('1');
    expect(headers.has('Authorization')).toBe(false);
    expect(headers.has('Cookie')).toBe(false);
  });

  it('accepts empty and twenty-row first pages with Unicode names at the name-length boundary', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    expect(await listKnowledgeBases(KB_WORKSPACE_ID, new AbortController().signal)).toEqual([]);
    const page = Array.from({ length: 20 }, (_, index) => ({
      id: `00000000-0000-0000-0000-${String(index + 10).padStart(12, '0')}`,
      workspace_id: KB_WORKSPACE_ID,
      name: index === 0 ? '😀'.repeat(200) : `知识库${index}`,
    }));
    fetchMock.mockResolvedValueOnce(jsonResponse(page));
    expect(await listKnowledgeBases(KB_WORKSPACE_ID, new AbortController().signal)).toEqual(page.map((item) => ({
      id: item.id, workspaceId: item.workspace_id, name: item.name,
    })));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    ['wrapped rows', { items: [KNOWLEDGE_BASE] }],
    ['null payload', null],
    ['non-object row', [KNOWLEDGE_BASE, null]],
    ['invalid ID', [KNOWLEDGE_BASE, { ...KNOWLEDGE_BASE, id: PRIVATE }]],
    ['ID with trailing newline', [{ ...KNOWLEDGE_BASE, id: `${KNOWLEDGE_BASE_ID}\n` }]],
    ['missing name', [{ id: KNOWLEDGE_BASE_ID, workspace_id: KB_WORKSPACE_ID }]],
    ['non-string name', [{ ...KNOWLEDGE_BASE, name: 123 }]],
    ['empty name', [{ ...KNOWLEDGE_BASE, name: '' }]],
    ['overlong name', [{ ...KNOWLEDGE_BASE, name: '😀'.repeat(201) }]],
    ['missing workspace ID', [{ id: KNOWLEDGE_BASE_ID, name: '知识库' }]],
    ['invalid workspace ID', [{ ...KNOWLEDGE_BASE, workspace_id: PRIVATE }]],
    ['different workspace', [KNOWLEDGE_BASE, { ...KNOWLEDGE_BASE, id: USER_ID, workspace_id: WORKSPACE_ID }]],
    ['duplicate normalized ID', [KNOWLEDGE_BASE, { ...KNOWLEDGE_BASE, id: KNOWLEDGE_BASE_ID.toUpperCase() }]],
    ['twenty-one rows', Array.from({ length: 21 }, (_, index) => ({
      ...KNOWLEDGE_BASE, id: `00000000-0000-0000-0000-${String(index + 10).padStart(12, '0')}`,
    }))],
  ])('rejects %s without returning a partial list', async (_label, payload) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(payload));
    await expectSafeFailure(
      listKnowledgeBases(KB_WORKSPACE_ID, new AbortController().signal),
      '后端返回的数据无效，请稍后重试。', null,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('rejects malformed workspace input before making a request', async () => {
    for (const workspaceId of [
      '', PRIVATE, ` ${KB_WORKSPACE_ID}`, `${KB_WORKSPACE_ID}\n`, `${KB_WORKSPACE_ID}/other`, KB_WORKSPACE_ID.slice(1),
    ]) {
      await expectSafeFailure(
        listKnowledgeBases(workspaceId, new AbortController().signal),
        '后端返回的数据无效，请稍后重试。', null,
      );
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    [401, '登录已失效，请重新登录。'],
    [404, '请求未完成，请稍后重试。'],
  ] as const)('preserves a safe HTTP %s error without reading response details', async (status, message) => {
    const response = jsonResponse({ detail: `${PRIVATE} ${PASSWORD}` }, status);
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    await expectSafeFailure(listKnowledgeBases(KB_WORKSPACE_ID, new AbortController().signal), message, status);
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('contains malformed JSON and network errors without retrying', async () => {
    fetchMock.mockResolvedValueOnce(new Response(`${PRIVATE} ${PASSWORD}`));
    await expectSafeFailure(
      listKnowledgeBases(KB_WORKSPACE_ID, new AbortController().signal),
      '后端返回的数据无效，请稍后重试。', null,
    );
    fetchMock.mockRejectedValueOnce(new Error(`${PRIVATE} ${PASSWORD}`));
    await expectSafeFailure(
      listKnowledgeBases(KB_WORKSPACE_ID, new AbortController().signal),
      '无法连接后端，请检查服务后重试。', null,
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('uses the shared timeout and cancellation boundary', async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementationOnce((_path, options) => new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
    }));
    const parent = new AbortController();
    const rejection = expectSafeFailure(
      listKnowledgeBases(KB_WORKSPACE_ID, parent.signal), '请求超时，请稍后重试。', null,
    );
    await vi.advanceTimersByTimeAsync(10_000);
    await rejection;
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    parent.abort();
    await expectSafeFailure(listKnowledgeBases(KB_WORKSPACE_ID, parent.signal), '请求已取消。', null);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
