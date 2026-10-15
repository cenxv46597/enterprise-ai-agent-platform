export type User = { id: string; username: string };
export type Workspace = { id: string; name: string };
export type Session = { token: string; user: User; expiresAt: number };

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
const TOKEN_PATTERN = /^[A-Za-z0-9._~-]{1,4096}$/;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID_PATTERN.test(value)) {
    throw new ApiError(INVALID_RESPONSE);
  }
  return value.toLowerCase();
}

async function request(
  path: string,
  options: RequestInit,
  parentSignal: AbortSignal,
  isLogin = false,
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
        credentials: "omit",
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

export async function login(username: string, password: string, signal: AbortSignal): Promise<Session> {
  const startedAt = Date.now();
  const normalized = username.trim().toLowerCase();
  const passwordLength = Array.from(password).length;
  if (!/^[a-z][a-z0-9_]{2,31}$/.test(normalized) || passwordLength < 1 || passwordLength > 128) {
    throw new ApiError("用户名或密码格式不正确。");
  }
  const tokenPayload = await request("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: normalized, password }),
  }, signal, true);
  if (!isObject(tokenPayload)
      || typeof tokenPayload.access_token !== "string"
      || !TOKEN_PATTERN.test(tokenPayload.access_token)
      || tokenPayload.token_type !== "bearer"
      || typeof tokenPayload.expires_in !== "number"
      || !Number.isInteger(tokenPayload.expires_in)
      || tokenPayload.expires_in < 1 || tokenPayload.expires_in > 1800) {
    throw new ApiError(INVALID_RESPONSE);
  }
  const token = tokenPayload.access_token;
  const me = await request("/api/v1/auth/me", {
    method: "GET", headers: { Authorization: `Bearer ${token}` },
  }, signal);
  if (!isObject(me) || me.username !== normalized) throw new ApiError(INVALID_RESPONSE);
  return {
    token,
    user: { id: uuid(me.id), username: normalized },
    expiresAt: startedAt + tokenPayload.expires_in * 1000,
  };
}

export async function listWorkspaces(token: string, signal: AbortSignal): Promise<Workspace[]> {
  if (typeof token !== "string" || !TOKEN_PATTERN.test(token)) throw new ApiError(SESSION_EXPIRED, 401);
  const payload = await request("/api/v1/workspaces?limit=20&offset=0", {
    method: "GET", headers: { Authorization: `Bearer ${token}` },
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
