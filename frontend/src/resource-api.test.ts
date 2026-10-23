import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createWorkspace, createKnowledgeBase, listWorkspaces, listKnowledgeBases,
  RESOURCE_CREATE_UNCONFIRMED } from './api';

const SPACE = '00000000-0000-0000-0000-000000000001';
const KB = '00000000-0000-0000-0000-000000000002';
const OTHER = '00000000-0000-0000-0000-000000000003';
const PRIVATE = 'PRIVATE_SERVER_DETAIL';
const fetchMock = vi.fn<typeof fetch>();
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});
beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('resource creation and server pagination', () => {
  it('preserves default pages and forwards explicit offsets to scoped GETs', async () => {
    fetchMock.mockImplementation(async () => json([]));
    const signal = new AbortController().signal;
    await listWorkspaces(signal); await listWorkspaces(signal, 20);
    await listKnowledgeBases(SPACE, signal, 40);
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      '/api/v1/workspaces?limit=20&offset=0', '/api/v1/workspaces?limit=20&offset=20',
      `/api/v1/workspaces/${SPACE}/knowledge-bases?limit=20&offset=40`,
    ]);
  });
  it.each([-20, 1, 20.5, NaN, Infinity, Number.MAX_SAFE_INTEGER])('rejects invalid page %s before dispatch', async offset => {
    await expect(listWorkspaces(new AbortController().signal, offset)).rejects.toBeInstanceOf(ApiError);
    await expect(listKnowledgeBases(SPACE, new AbortController().signal, offset)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it('requires 201 and exact identity, trims names, and keeps only public fields', async () => {
    fetchMock.mockResolvedValueOnce(json({ id: SPACE, name: '演示🙂', raw: PRIVATE }, 201));
    expect(await createWorkspace(' 演示🙂\n', new AbortController().signal)).toEqual({ id: SPACE, name: '演示🙂' });
    fetchMock.mockResolvedValueOnce(json({ id: KB, workspace_id: SPACE, name: '资料库', raw: PRIVATE }, 201));
    expect(await createKnowledgeBase(SPACE, '资料库', new AbortController().signal))
      .toEqual({ id: KB, workspaceId: SPACE, name: '资料库' });
    for (const [, init] of fetchMock.mock.calls) {
      expect(init).toMatchObject({ method: 'POST', credentials: 'same-origin', redirect: 'error', cache: 'no-store' });
      expect(new Headers(init?.headers).get('X-EAAP-Web')).toBe('1');
      expect(new Headers(init?.headers).has('Authorization')).toBe(false);
    }
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({ name: '演示🙂' });
  });
  it.each(['', ' \n ', '🙂'.repeat(201), 'a\0b'])('invalid names do not dispatch', async name => {
    await expect(createWorkspace(name, new AbortController().signal)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it.each([401, 403, 404, 409, 422])('returns a safe rejection for HTTP %s without reading its body', async status => {
    const response = json({ detail: PRIVATE }, status), read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    await expect(createKnowledgeBase(SPACE, '资料库', new AbortController().signal))
      .rejects.toMatchObject({ status });
    expect(read).not.toHaveBeenCalled(); expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it.each([
    { id: KB, workspace_id: OTHER, name: '资料库' }, { id: 'bad', workspace_id: SPACE, name: '资料库' },
    { id: KB, workspace_id: SPACE, name: '被改名' }, {},
  ])('rejects unverifiable creation acknowledgments without entering another scope', async payload => {
    fetchMock.mockResolvedValueOnce(json(payload, 201));
    await expect(createKnowledgeBase(SPACE, '资料库', new AbortController().signal))
      .rejects.toMatchObject({ message: RESOURCE_CREATE_UNCONFIRMED });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('treats network, unexpected success, and bad JSON as unknown, without retry', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError(PRIVATE))
      .mockResolvedValueOnce(json({ id: SPACE, name: '演示' }, 200))
      .mockResolvedValueOnce(new Response(PRIVATE, { status: 201 }));
    for (let i = 0; i < 3; i++) await expect(createWorkspace('演示', new AbortController().signal))
      .rejects.toMatchObject({ message: RESOURCE_CREATE_UNCONFIRMED });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
  it('a timed-out POST remains unknown even when a late response ignores abort', async () => {
    vi.useFakeTimers();
    let resolve!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise<Response>(done => { resolve = done; }));
    const operation = createWorkspace('演示', new AbortController().signal);
    const rejection = expect(operation).rejects.toMatchObject({ message: RESOURCE_CREATE_UNCONFIRMED });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    resolve(json({ id: SPACE, name: '演示' }, 201)); await rejection;
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
