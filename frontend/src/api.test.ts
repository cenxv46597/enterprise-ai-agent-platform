import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, listWorkspaces, login } from "./api";

const TOKEN = "controlled.web-token_123";
const PASSWORD = " private-web-test-password ";
const PRIVATE = "PRIVATE-RESPONSE-DETAIL";
const USER_ID = "00000000-0000-0000-0000-000000000001";
const WORKSPACE_ID = "00000000-0000-0000-0000-000000000011";
const TOKEN_PAYLOAD = { access_token: TOKEN, token_type: "bearer", expires_in: 1800 };
const ME = { id: USER_ID, username: "demo_a", created_at: "2026-10-15T00:00:00Z" };
const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

async function expectSafeFailure(action: Promise<unknown>, message: string, status: number | null): Promise<void> {
  const error: unknown = await action.then(() => {
    throw new Error("Expected API rejection");
  }, (failure: unknown) => failure);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({ message, status });
  for (const secret of [PASSWORD, TOKEN, PRIVATE]) expect(String(error)).not.toContain(secret);
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("same-origin API boundary", () => {
  it("normalizes username, preserves password and binds the workspace request to the verified token", async () => {
    let now = 1000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    fetchMock.mockImplementationOnce(async () => {
      now = 5000;
      return jsonResponse(TOKEN_PAYLOAD);
    }).mockResolvedValueOnce(jsonResponse(ME)).mockResolvedValueOnce(jsonResponse([
      { id: WORKSPACE_ID, name: "研发空间", created_at: "ignored", owner: "not-returned" },
    ]));
    const signal = new AbortController().signal;
    const session = await login(" DEMO_A ", PASSWORD, signal);
    expect(session).toEqual({ token: TOKEN, user: { id: USER_ID, username: "demo_a" }, expiresAt: 1_801_000 });
    expect(await listWorkspaces(session.token, signal)).toEqual([{ id: WORKSPACE_ID, name: "研发空间" }]);
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/auth/login", "/api/v1/auth/me", "/api/v1/workspaces?limit=20&offset=0",
    ]);
    const first = fetchMock.mock.calls[0][1];
    expect(first?.method).toBe("POST");
    expect(JSON.parse(String(first?.body))).toEqual({ username: "demo_a", password: PASSWORD });
    expect(new Headers(first?.headers).has("Authorization")).toBe(false);
    for (const [, options] of fetchMock.mock.calls) {
      expect(options).toMatchObject({ mode: "same-origin", credentials: "omit", redirect: "error", cache: "no-store" });
      expect(options?.signal).toBeInstanceOf(AbortSignal);
    }
    for (const [, options] of fetchMock.mock.calls.slice(1)) {
      expect(options?.method).toBe("GET");
      expect(new Headers(options?.headers).get("Authorization")).toBe(`Bearer ${TOKEN}`);
    }
  });

  it("rejects invalid login fields before making any request", async () => {
    for (const [username, password] of [["bad name", PASSWORD], ["demo_a", ""], ["demo_a", "x".repeat(129)]] as const) {
      await expectSafeFailure(login(username, password, new AbortController().signal), "用户名或密码格式不正确。", null);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not request identity after an unsafe token or invalid token protocol", async () => {
    for (const change of [
      { access_token: `${TOKEN}\n${PRIVATE}` }, { token_type: "Bearer" },
      { expires_in: true }, { expires_in: 1801 },
    ]) {
      fetchMock.mockReset().mockResolvedValueOnce(jsonResponse({ ...TOKEN_PAYLOAD, ...change }));
      await expectSafeFailure(login("demo_a", PASSWORD, new AbortController().signal), "后端返回的数据无效，请稍后重试。", null);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    }
  });

  it("rejects a mismatched identity or invalid UUID before returning a session", async () => {
    for (const change of [{ username: "someone_else" }, { id: PRIVATE }]) {
      fetchMock.mockReset().mockResolvedValueOnce(jsonResponse(TOKEN_PAYLOAD))
        .mockResolvedValueOnce(jsonResponse({ ...ME, ...change }));
      await expectSafeFailure(login("demo_a", PASSWORD, new AbortController().signal), "后端返回的数据无效，请稍后重试。", null);
      expect(fetchMock).toHaveBeenCalledTimes(2);
    }
  });

  it.each([
    ["login", 401, "用户名或密码不正确。"],
    ["workspaces", 401, "登录已失效，请重新登录。"],
    ["workspaces", 503, "请求未完成，请稍后重试。"],
  ] as const)("reports fixed %s HTTP %s errors without reading server details", async (stage, status, message) => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: `${PRIVATE} ${PASSWORD} ${TOKEN}` }, status));
    const signal = new AbortController().signal;
    const action = stage === "login" ? login("demo_a", PASSWORD, signal) : listWorkspaces(TOKEN, signal);
    await expectSafeFailure(action, message, status);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("contains malformed JSON and network exceptions in safe errors", async () => {
    fetchMock.mockResolvedValueOnce(new Response(`${PRIVATE} ${PASSWORD} ${TOKEN}`));
    await expectSafeFailure(listWorkspaces(TOKEN, new AbortController().signal), "后端返回的数据无效，请稍后重试。", null);
    fetchMock.mockRejectedValueOnce(new Error(`${PRIVATE} ${PASSWORD} ${TOKEN}`));
    await expectSafeFailure(listWorkspaces(TOKEN, new AbortController().signal), "无法连接后端，请检查服务后重试。", null);
  });

  it("rejects malformed or duplicate workspace rows without returning a partial list", async () => {
    for (const payload of [
      [{ id: PRIVATE, name: "空间" }], [{ id: WORKSPACE_ID, name: "" }],
      [{ id: WORKSPACE_ID, name: "空间一" }, { id: WORKSPACE_ID, name: "空间二" }],
      Array.from({ length: 21 }, () => ({ id: WORKSPACE_ID, name: "空间" })),
    ]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      await expectSafeFailure(listWorkspaces(TOKEN, new AbortController().signal), "后端返回的数据无效，请稍后重试。", null);
    }
  });

  it("accepts empty and full first pages without inventing totals or roles", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    expect(await listWorkspaces(TOKEN, new AbortController().signal)).toEqual([]);
    const page = Array.from({ length: 20 }, (_, index) => ({
      id: `00000000-0000-0000-0000-${String(index + 10).padStart(12, "0")}`, name: `空间${index}`,
    }));
    fetchMock.mockResolvedValueOnce(jsonResponse(page));
    expect(await listWorkspaces(TOKEN, new AbortController().signal)).toEqual(page);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("propagates parent cancellation and makes no subsequent identity request", async () => {
    fetchMock.mockImplementationOnce((_path, options) => new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => reject(new Error(PRIVATE)), { once: true });
    }));
    const parent = new AbortController();
    const rejection = expectSafeFailure(login("demo_a", PASSWORD, parent.signal), "请求已取消。", null);
    parent.abort(PRIVATE);
    await rejection;
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    await expectSafeFailure(login("demo_a", PASSWORD, parent.signal), "请求已取消。", null);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("aborts a request at ten seconds and cleans its timer", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementationOnce((_path, options) => new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => reject(new Error(PRIVATE)), { once: true });
    }));
    const rejection = expectSafeFailure(listWorkspaces(TOKEN, new AbortController().signal), "请求超时，请稍后重试。", null);
    await vi.advanceTimersByTimeAsync(10_000);
    await rejection;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
