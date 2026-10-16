export type User = { id: string; username: string };
export type Workspace = { id: string; name: string };
export type KnowledgeBase = { id: string; workspaceId: string; name: string };
export type Session = { user: User; expiresAt: number };

export class ApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const INVALID_RESPONSE = "后端返回的数据无效，请稍后重试。";
const SESSION_EXPIRED = "登录已失效，请重新登录。";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID_PATTERN.test(value)) {
    throw new ApiError(INVALID_RESPONSE);
  }
  return value.toLowerCase();
}

function strictUuid(value: unknown): string {
  const normalized = uuid(value);
  if (normalized.length !== 36) throw new ApiError(INVALID_RESPONSE);
  return normalized;
}

async function request(
  path: string,
  options: RequestInit,
  parentSignal: AbortSignal,
  isLogin = false,
  noContent = false,
): Promise<unknown> {
  if (parentSignal.aborted) throw new ApiError("请求已取消。");
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  parentSignal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 10_000);
  const interrupted = () => new ApiError(timedOut ? "请求超时，请稍后重试。" : "请求已取消。");

  try {
    let response: Response;
    try {
      response = await fetch(path, {
        ...options,
        mode: "same-origin",
        credentials: "same-origin",
        headers: { ...options.headers, "X-EAAP-Web": "1" },
        redirect: "error",
        cache: "no-store",
        signal: controller.signal,
      });
    } catch {
      if (controller.signal.aborted) throw interrupted();
      throw new ApiError("无法连接后端，请检查服务后重试。");
    }
    if (controller.signal.aborted) throw interrupted();
    if (!response.ok) {
      const message = response.status === 401
        ? (isLogin ? "用户名或密码不正确。" : SESSION_EXPIRED)
        : "请求未完成，请稍后重试。";
      throw new ApiError(message, response.status);
    }
    if (noContent) {
      if (response.status !== 204) throw new ApiError(INVALID_RESPONSE);
      return null;
    }
    try {
      const payload: unknown = await response.json();
      if (controller.signal.aborted) throw interrupted();
      return payload;
    } catch {
      if (controller.signal.aborted) throw interrupted();
      throw new ApiError(INVALID_RESPONSE);
    }
  } finally {
    clearTimeout(timer);
    parentSignal.removeEventListener("abort", cancel);
  }
}

function parseSession(payload: unknown): Session {
  if (!isObject(payload) || !isObject(payload.user)
      || typeof payload.user.username !== "string"
      || !/^[a-z][a-z0-9_]{2,31}$/.test(payload.user.username)
      || typeof payload.expires_at !== "number" || !Number.isSafeInteger(payload.expires_at)) {
    throw new ApiError(INVALID_RESPONSE);
  }
  const expiresAt = payload.expires_at * 1000;
  const now = Date.now();
  if (expiresAt <= now || expiresAt > now + 1_800_000) throw new ApiError(INVALID_RESPONSE);
  return { user: { id: uuid(payload.user.id), username: payload.user.username }, expiresAt };
}

export async function login(username: string, password: string, signal: AbortSignal): Promise<Session> {
  const normalized = username.trim().toLowerCase();
  const passwordLength = Array.from(password).length;
  if (!/^[a-z][a-z0-9_]{2,31}$/.test(normalized) || passwordLength < 1 || passwordLength > 128) {
    throw new ApiError("用户名或密码格式不正确。");
  }
  const payload = await request("/api/v1/auth/web/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: normalized, password }),
  }, signal, true);
  const session = parseSession(payload);
  if (session.user.username !== normalized) throw new ApiError(INVALID_RESPONSE);
  return session;
}

export async function restoreSession(signal: AbortSignal): Promise<Session | null> {
  try {
    return parseSession(await request("/api/v1/auth/web/session", { method: "GET" }, signal));
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 401) return null;
    throw failure;
  }
}

export async function logout(signal: AbortSignal): Promise<void> {
  await request("/api/v1/auth/web/logout", { method: "POST" }, signal, false, true);
}

export async function listWorkspaces(signal: AbortSignal): Promise<Workspace[]> {
  const payload = await request("/api/v1/workspaces?limit=20&offset=0", {
    method: "GET",
  }, signal);
  if (!Array.isArray(payload) || payload.length > 20) throw new ApiError(INVALID_RESPONSE);
  const seen = new Set<string>();
  return payload.map((item: unknown) => {
    if (!isObject(item) || typeof item.name !== "string"
        || Array.from(item.name).length < 1 || Array.from(item.name).length > 200) {
      throw new ApiError(INVALID_RESPONSE);
    }
    const id = uuid(item.id);
    if (seen.has(id)) throw new ApiError(INVALID_RESPONSE);
    seen.add(id);
    return { id, name: item.name };
  });
}

export async function listKnowledgeBases(workspaceId: string, signal: AbortSignal): Promise<KnowledgeBase[]> {
  const normalizedWorkspaceId = strictUuid(workspaceId);
  const payload = await request(`/api/v1/workspaces/${normalizedWorkspaceId}/knowledge-bases?limit=20&offset=0`, {
    method: "GET",
  }, signal);
  if (!Array.isArray(payload) || payload.length > 20) throw new ApiError(INVALID_RESPONSE);
  const seen = new Set<string>();
  return payload.map((item: unknown) => {
    if (!isObject(item) || typeof item.name !== "string"
        || Array.from(item.name).length < 1 || Array.from(item.name).length > 200) {
      throw new ApiError(INVALID_RESPONSE);
    }
    const id = strictUuid(item.id);
    const itemWorkspaceId = strictUuid(item.workspace_id);
    if (itemWorkspaceId !== normalizedWorkspaceId || seen.has(id)) throw new ApiError(INVALID_RESPONSE);
    seen.add(id);
    return { id, workspaceId: itemWorkspaceId, name: item.name };
  });
}
