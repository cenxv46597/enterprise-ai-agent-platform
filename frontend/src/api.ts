export type User = { id: string; username: string };
export type Workspace = { id: string; name: string };
export type KnowledgeBase = { id: string; workspaceId: string; name: string };
export type DocumentStatus = "uploaded" | "processing" | "parsed" | "ready" | "failed";
export type DocumentStatusFilter = "all" | DocumentStatus;
export type KnowledgeDocument = {
  id: string;
  knowledgeBaseId: string;
  originalFilename: string;
  status: DocumentStatus;
};
export type UploadedDocument = KnowledgeDocument & {
  status: "parsed" | "failed";
  parseError: string | null;
};
export type DocumentChunk = {
  id: string;
  documentId: string;
  chunkIndex: number;
  content: string;
  startChar: number;
  endChar: number;
};
export type DocumentEmbedding = {
  documentId: string;
  status: "ready";
  model: string;
  dimensions: 1024;
  chunkCount: number;
  reused: boolean;
};
export type SearchStrategy = "vector" | "hybrid";
export type SearchHit = {
  chunkId: string;
  documentId: string;
  documentName: string;
  chunkIndex: number;
  content: string;
  startChar: number;
  endChar: number;
  distance: number;
  fusionScore: number | null;
  vectorRank: number | null;
  lexicalRank: number | null;
};
export type KnowledgeSearchResult = {
  strategy: SearchStrategy;
  metric: "cosine_distance" | "reciprocal_rank_fusion";
  model: string | null;
  dimensions: 1024 | null;
  hits: SearchHit[];
};
export type AnswerCitation = {
  chunkId: string;
  documentId: string;
  documentName: string;
  chunkIndex: number;
  quote: string;
  startChar: number;
  endChar: number;
};
export type KnowledgeAnswerResult = {
  answer: string;
  abstained: boolean;
  citations: AnswerCitation[];
};
export type Session = { user: User; expiresAt: number };

function safeTraceId(value: unknown): string | null {
  // The server contract is UUID4. Length also rejects JS $ matching before a final newline.
  return typeof value === "string" && value.length === 36
    && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
    ? value.toLowerCase() : null;
}

export function responseTraceId(response: Response): string | null {
  try {
    return safeTraceId(response.headers.get("X-Trace-Id"));
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  readonly status: number | null;
  readonly traceId: string | null;

  constructor(message: string, status: number | null = null, traceId: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.traceId = safeTraceId(traceId);
  }
}

export const UPLOAD_UNCONFIRMED_MESSAGE = "上传结果未确认；未自动重试。请先核对知识库中的文档记录，再决定是否再次上传。";

export class UploadUnconfirmedError extends ApiError {
  constructor(status: number | null = null) {
    super(UPLOAD_UNCONFIRMED_MESSAGE, status);
    this.name = "UploadUnconfirmedError";
  }
}

export const CHUNK_UNCONFIRMED_MESSAGE = "切块结果未确认；未自动重试。请刷新文档与切块记录后再决定是否重新执行。";

export class ChunkUnconfirmedError extends ApiError {
  constructor(status: number | null = null) {
    super(CHUNK_UNCONFIRMED_MESSAGE, status);
    this.name = "ChunkUnconfirmedError";
  }
}

export const EMBEDDING_UNCONFIRMED_MESSAGE = "向量化结果未确认；可能已调用模型或保存结果，未自动重试。请先刷新文档与切块核对状态，不要立即重复提交。";

export class EmbeddingUnconfirmedError extends ApiError {
  constructor(status: number | null = null) {
    super(EMBEDDING_UNCONFIRMED_MESSAGE, status);
    this.name = "EmbeddingUnconfirmedError";
  }
}

export const SEARCH_UNCONFIRMED_MESSAGE = "检索结果未确认；可能已调用向量服务，未自动重试。";

export class SearchUnconfirmedError extends ApiError {
  constructor(status: number | null = null) {
    super(SEARCH_UNCONFIRMED_MESSAGE, status);
    this.name = "SearchUnconfirmedError";
  }
}

export const ANSWER_UNCONFIRMED_MESSAGE = "问答结果未确认；本次可能已调用向量或生成服务，未自动重试。";

export class AnswerUnconfirmedError extends ApiError {
  constructor(status: number | null = null, traceId: unknown = null) {
    super(ANSWER_UNCONFIRMED_MESSAGE, status, traceId);
    this.name = "AnswerUnconfirmedError";
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

export function strictUuid(value: unknown): string {
  const normalized = uuid(value);
  if (normalized.length !== 36) throw new ApiError(INVALID_RESPONSE);
  return normalized;
}

function isDocumentStatus(value: unknown): value is DocumentStatus {
  return value === "uploaded" || value === "processing" || value === "parsed"
    || value === "ready" || value === "failed";
}

export function isDocumentStatusFilter(value: unknown): value is DocumentStatusFilter {
  return value === "all" || isDocumentStatus(value);
}

export function isSearchStrategy(value: unknown): value is SearchStrategy {
  return value === "vector" || value === "hybrid";
}

export function webRequestOptions(options: RequestInit, signal: AbortSignal): RequestInit {
  return {
    ...options,
    mode: "same-origin",
    credentials: "same-origin",
    headers: { ...options.headers, "X-EAAP-Web": "1" },
    redirect: "error",
    cache: "no-store",
    signal,
  };
}

async function request(
  path: string,
  options: RequestInit,
  parentSignal: AbortSignal,
  isLogin = false,
  noContent = false,
  expectedStatus?: number,
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
      response = await fetch(path, webRequestOptions(options, controller.signal));
    } catch {
      if (controller.signal.aborted) throw interrupted();
      throw new ApiError("无法连接后端，请检查服务后重试。");
    }
    if (controller.signal.aborted) throw interrupted();
    if (!response.ok) {
      const message = response.status === 401
        ? (isLogin ? "用户名或密码不正确。" : SESSION_EXPIRED)
        : "请求未完成，请稍后重试。";
      throw new ApiError(message, response.status, responseTraceId(response));
    }
    if (noContent) {
      if (response.status !== 204) throw new ApiError(INVALID_RESPONSE);
      return null;
    }
    if (expectedStatus !== undefined && response.status !== expectedStatus) {
      throw new ApiError(INVALID_RESPONSE, null, responseTraceId(response));
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

export const DOCUMENT_PAGE_SIZE = 20;
export const MAX_DOCUMENT_OFFSET = Number.MAX_SAFE_INTEGER - Number.MAX_SAFE_INTEGER % DOCUMENT_PAGE_SIZE;
export const DOCUMENT_FILENAME_QUERY_ERROR = "文件名线索不能超过 255 个字符，也不能包含空字符。";

export function isDocumentOffset(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value)
    && value >= 0 && value <= MAX_DOCUMENT_OFFSET && value % DOCUMENT_PAGE_SIZE === 0;
}

export function normalizeDocumentFilenameQuery(value: unknown): string {
  if (typeof value !== "string") throw new ApiError(DOCUMENT_FILENAME_QUERY_ERROR);
  const normalized = value.trim();
  if (normalized.includes("\u0000") || Array.from(normalized).length > 255) {
    throw new ApiError(DOCUMENT_FILENAME_QUERY_ERROR);
  }
  return normalized;
}

export async function listDocuments(
  workspaceId: string,
  knowledgeBaseId: string,
  signal: AbortSignal,
  offset = 0,
  filenameQuery = "",
  statusFilter: DocumentStatusFilter = "all",
): Promise<KnowledgeDocument[]> {
  const normalizedWorkspaceId = strictUuid(workspaceId);
  const normalizedKnowledgeBaseId = strictUuid(knowledgeBaseId);
  if (!isDocumentOffset(offset)) throw new ApiError("文档列表页码无效，请返回首页后重试。");
  const normalizedFilenameQuery = normalizeDocumentFilenameQuery(filenameQuery);
  if (!isDocumentStatusFilter(statusFilter)) throw new ApiError("文档状态条件无效，请重新选择。");
  const query = new URLSearchParams({ limit: String(DOCUMENT_PAGE_SIZE), offset: String(offset) });
  if (normalizedFilenameQuery) query.set("filename_query", normalizedFilenameQuery);
  if (statusFilter !== "all") query.set("status", statusFilter);
  const payload = await request(
    `/api/v1/workspaces/${normalizedWorkspaceId}/knowledge-bases/${normalizedKnowledgeBaseId}/documents?${query}`,
    { method: "GET" }, signal,
  );
  if (!Array.isArray(payload) || payload.length > DOCUMENT_PAGE_SIZE) throw new ApiError(INVALID_RESPONSE);
  const seen = new Set<string>();
  return payload.map((item: unknown) => {
    if (!isObject(item) || typeof item.original_filename !== "string"
        || Array.from(item.original_filename).length < 1 || Array.from(item.original_filename).length > 255
        || !isDocumentStatus(item.status) || (statusFilter !== "all" && item.status !== statusFilter)) {
      throw new ApiError(INVALID_RESPONSE);
    }
    const id = strictUuid(item.id);
    const itemKnowledgeBaseId = strictUuid(item.knowledge_base_id);
    if (itemKnowledgeBaseId !== normalizedKnowledgeBaseId || seen.has(id)) throw new ApiError(INVALID_RESPONSE);
    seen.add(id);
    return {
      id, knowledgeBaseId: itemKnowledgeBaseId, originalFilename: item.original_filename, status: item.status,
    };
  });
}

function documentPath(workspaceId: string, knowledgeBaseId: string, documentId: string): string {
  return `/api/v1/workspaces/${strictUuid(workspaceId)}/knowledge-bases/${strictUuid(knowledgeBaseId)}/documents/${strictUuid(documentId)}`;
}

export async function getDocument(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
  signal: AbortSignal,
): Promise<KnowledgeDocument> {
  const path = documentPath(workspaceId, knowledgeBaseId, documentId);
  const payload = await request(path, { method: "GET" }, signal, false, false, 200);
  if (!isObject(payload) || typeof payload.original_filename !== "string"
      || Array.from(payload.original_filename).length < 1 || Array.from(payload.original_filename).length > 255
      || !isDocumentStatus(payload.status)) throw new ApiError(INVALID_RESPONSE);
  const id = strictUuid(payload.id);
  const returnedKnowledgeBaseId = strictUuid(payload.knowledge_base_id);
  if (id !== strictUuid(documentId) || returnedKnowledgeBaseId !== strictUuid(knowledgeBaseId)) {
    throw new ApiError(INVALID_RESPONSE);
  }
  return {
    id, knowledgeBaseId: returnedKnowledgeBaseId, originalFilename: payload.original_filename, status: payload.status,
  };
}

const CHUNK_CHARACTER_LIMIT = 10 * 1024 * 1024;

function parseDocumentChunk(payload: unknown, documentId: string, characterLimit = CHUNK_CHARACTER_LIMIT): DocumentChunk {
  if (!isObject(payload) || !Number.isSafeInteger(payload.chunk_index) || (payload.chunk_index as number) < 1
      || typeof payload.content !== "string" || payload.content.length === 0
      || !Number.isSafeInteger(payload.start_char) || !Number.isSafeInteger(payload.end_char)) {
    throw new ApiError(INVALID_RESPONSE);
  }
  const startChar = payload.start_char as number;
  const endChar = payload.end_char as number;
  if (startChar < 0 || endChar <= startChar) throw new ApiError(INVALID_RESPONSE);
  const id = strictUuid(payload.id);
  const returnedDocumentId = strictUuid(payload.document_id);
  if (returnedDocumentId !== documentId) throw new ApiError(INVALID_RESPONSE);
  let characterCount = 0;
  for (const _character of payload.content) {
    if (++characterCount > characterLimit) throw new ApiError(INVALID_RESPONSE);
  }
  if (characterCount !== endChar - startChar) throw new ApiError(INVALID_RESPONSE);
  return { id, documentId: returnedDocumentId, chunkIndex: payload.chunk_index as number,
    content: payload.content, startChar, endChar };
}

function parseDocumentChunks(payload: unknown, documentId: string, allowEmpty: boolean): DocumentChunk[] {
  if (!Array.isArray(payload) || payload.length > 10_000 || (!allowEmpty && payload.length === 0)) {
    throw new ApiError(INVALID_RESPONSE);
  }
  const seen = new Set<string>();
  let totalCharacters = 0;
  let previousStart = -1;
  let previousEnd = -1;
  return payload.map((item: unknown, index: number) => {
    const chunk = parseDocumentChunk(item, documentId, CHUNK_CHARACTER_LIMIT - totalCharacters);
    if (chunk.chunkIndex !== index + 1 || seen.has(chunk.id)
        || (index === 0 && chunk.startChar !== 0)
        || (index > 0 && (chunk.startChar <= previousStart || chunk.endChar <= previousEnd || chunk.startChar > previousEnd))) {
      throw new ApiError(INVALID_RESPONSE);
    }
    totalCharacters += chunk.endChar - chunk.startChar;
    if (totalCharacters > CHUNK_CHARACTER_LIMIT) throw new ApiError(INVALID_RESPONSE);
    previousStart = chunk.startChar;
    previousEnd = chunk.endChar;
    seen.add(chunk.id);
    return chunk;
  });
}

export async function listDocumentChunks(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
  signal: AbortSignal,
): Promise<DocumentChunk[]> {
  const path = `${documentPath(workspaceId, knowledgeBaseId, documentId)}/chunks`;
  return parseDocumentChunks(await request(path, { method: "GET" }, signal, false, false, 200), strictUuid(documentId), true);
}

export async function getDocumentChunk(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
  chunkId: string,
  signal: AbortSignal,
): Promise<DocumentChunk> {
  const expectedChunkId = strictUuid(chunkId);
  const path = `${documentPath(workspaceId, knowledgeBaseId, documentId)}/chunks/${expectedChunkId}`;
  const chunk = parseDocumentChunk(await request(path, { method: "GET" }, signal, false, false, 200), strictUuid(documentId));
  if (chunk.id !== expectedChunkId) throw new ApiError(INVALID_RESPONSE);
  return chunk;
}

export async function createDocumentChunks(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
  signal: AbortSignal,
): Promise<DocumentChunk[]> {
  const path = `${documentPath(workspaceId, knowledgeBaseId, documentId)}/chunks`;
  if (signal.aborted) throw new ApiError("请求已取消。");
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, 10_000);
  try {
    const response = await fetch(path, webRequestOptions({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chunk_size: 500, overlap: 50 }),
    }, controller.signal));
    if (controller.signal.aborted) throw new ChunkUnconfirmedError();
    const rejectionMessages: Record<number, string> = {
      401: SESSION_EXPIRED,
      403: "切块请求被拒绝，请检查登录与浏览器来源后重试。",
      404: "该文档不存在或你已无权访问，请返回文档列表。",
      409: "当前文档状态或已有切块配置不允许执行本次切块，请刷新文档与切块记录。",
      422: "切块请求未被接受，参数或单次处理量可能超过后端限制。",
    };
    if (Object.hasOwn(rejectionMessages, response.status)) {
      throw new ApiError(rejectionMessages[response.status], response.status);
    }
    if (response.status !== 200) throw new ChunkUnconfirmedError(response.status);
    const payload: unknown = await response.json();
    if (controller.signal.aborted) throw new ChunkUnconfirmedError();
    try {
      return parseDocumentChunks(payload, strictUuid(documentId), false);
    } catch {
      throw new ChunkUnconfirmedError();
    }
  } catch (failure) {
    if (failure instanceof ApiError) throw failure;
    throw new ChunkUnconfirmedError();
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
}

export async function createDocumentEmbeddings(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
  maxChunks: number,
  signal: AbortSignal,
): Promise<DocumentEmbedding> {
  const path = `${documentPath(workspaceId, knowledgeBaseId, documentId)}/embeddings`;
  if (!Number.isSafeInteger(maxChunks) || maxChunks < 1 || maxChunks > 1000) {
    throw new ApiError("请明确填写本次最多新生成的块数，范围为 1 到 1000 的整数。");
  }
  if (signal.aborted) throw new ApiError("请求已取消。");
  const normalizedDocumentId = strictUuid(documentId);
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, 330_000);
  try {
    const response = await fetch(path, webRequestOptions({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_chunks: maxChunks }),
    }, controller.signal));
    if (controller.signal.aborted) throw new EmbeddingUnconfirmedError();
    const rejectionMessages: Record<number, string> = {
      401: SESSION_EXPIRED,
      403: "向量化请求被拒绝，请检查登录与浏览器来源。",
      404: "该文档不存在或你已无权访问，请返回文档列表。",
      409: "当前文档状态或向量配置不允许完成本次操作，请刷新文档与切块核对状态；本响应不能确认是否已产生费用。",
      422: "请核对向量化参数、已有块数与本次新生成上限；本响应不能确认是否已产生费用。",
    };
    if (Object.hasOwn(rejectionMessages, response.status)) {
      throw new ApiError(rejectionMessages[response.status], response.status);
    }
    if (response.status !== 200) throw new EmbeddingUnconfirmedError(response.status);
    const payload: unknown = await response.json();
    if (controller.signal.aborted) throw new EmbeddingUnconfirmedError();
    try {
      if (!isObject(payload) || strictUuid(payload.document_id) !== normalizedDocumentId
          || payload.status !== "ready" || payload.dimensions !== 1024
          || typeof payload.model !== "string" || !payload.model.trim()
          || Array.from(payload.model).length > 200
          || !Number.isSafeInteger(payload.chunk_count)
          || (payload.chunk_count as number) < 1 || (payload.chunk_count as number) > 10_000
          || typeof payload.reused !== "boolean"
          || (!payload.reused && (payload.chunk_count as number) > maxChunks)) {
        throw new EmbeddingUnconfirmedError();
      }
      return {
        documentId: normalizedDocumentId, status: "ready", model: payload.model, dimensions: 1024,
        chunkCount: payload.chunk_count as number, reused: payload.reused,
      };
    } catch {
      throw new EmbeddingUnconfirmedError();
    }
  } catch (failure) {
    if (failure instanceof ApiError) throw failure;
    throw new EmbeddingUnconfirmedError();
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
}

function isSearchChannelRank(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 20);
}

function parseKnowledgeSearch(payload: unknown, expectedStrategy: SearchStrategy): KnowledgeSearchResult {
  const metric = expectedStrategy === "vector" ? "cosine_distance" : "reciprocal_rank_fusion";
  if (!isObject(payload) || payload.metric !== metric || payload.strategy !== expectedStrategy
      || (payload.reranking !== undefined && payload.reranking !== null)
      || !Array.isArray(payload.results) || payload.results.length > 3) {
    throw new SearchUnconfirmedError();
  }
  const emptyProfile = payload.results.length === 0 && payload.model === null && payload.dimensions === null;
  if (!emptyProfile && (typeof payload.model !== "string" || !payload.model.trim()
      || Array.from(payload.model).length > 200 || payload.dimensions !== 1024)) {
    throw new SearchUnconfirmedError();
  }
  const seen = new Set<string>();
  const documents = new Map<string, string>();
  let totalCharacters = 0;
  let previousDistance = -1;
  let previousFusionScore = Infinity;
  const hits = payload.results.map((item: unknown): SearchHit => {
    if (!isObject(item) || typeof item.document_name !== "string"
        || Array.from(item.document_name).length < 1 || Array.from(item.document_name).length > 255
        || !Number.isSafeInteger(item.chunk_index) || (item.chunk_index as number) < 1
        || typeof item.content !== "string" || item.content.length === 0
        || !Number.isSafeInteger(item.start_char) || !Number.isSafeInteger(item.end_char)
        || typeof item.distance !== "number" || !Number.isFinite(item.distance)
        || item.distance < 0 || item.distance > 2
        || (item.retrieval_rank !== undefined && item.retrieval_rank !== null)
        || (item.rerank_rank !== undefined && item.rerank_rank !== null)
        || (expectedStrategy === "vector" && item.distance < previousDistance)) {
      throw new SearchUnconfirmedError();
    }
    let fusionScore: number | null = null;
    let vectorRank: number | null = null;
    let lexicalRank: number | null = null;
    if (expectedStrategy === "hybrid") {
      const vectorRankValue = item.vector_rank;
      const lexicalRankValue = item.lexical_rank;
      if (typeof item.fusion_score !== "number" || !Number.isFinite(item.fusion_score) || item.fusion_score <= 0
          || item.fusion_score > previousFusionScore
          || !isSearchChannelRank(vectorRankValue) || !isSearchChannelRank(lexicalRankValue)
          || (vectorRankValue === null && lexicalRankValue === null)) {
        throw new SearchUnconfirmedError();
      }
      // ADR 0014 fixes the current RRF contribution at 1/(60 + channel rank).
      // Check this response's score; keep the server's order rather than sorting locally.
      const expectedScore = (vectorRankValue === null ? 0 : 1 / (60 + vectorRankValue))
        + (lexicalRankValue === null ? 0 : 1 / (60 + lexicalRankValue));
      if (Math.abs(item.fusion_score - expectedScore) > 1e-12) throw new SearchUnconfirmedError();
      fusionScore = item.fusion_score;
      vectorRank = vectorRankValue;
      lexicalRank = lexicalRankValue;
      previousFusionScore = fusionScore;
    }
    const startChar = item.start_char as number;
    const endChar = item.end_char as number;
    if (startChar < 0 || endChar <= startChar) throw new SearchUnconfirmedError();
    const chunkId = strictUuid(item.chunk_id);
    const documentId = strictUuid(item.document_id);
    if (seen.has(chunkId) || (documents.has(documentId) && documents.get(documentId) !== item.document_name)) {
      throw new SearchUnconfirmedError();
    }
    let characterCount = 0;
    for (const _character of item.content) {
      characterCount += 1;
      if (totalCharacters + characterCount > 100_000) throw new SearchUnconfirmedError();
    }
    if (characterCount !== endChar - startChar) throw new SearchUnconfirmedError();
    totalCharacters += characterCount;
    seen.add(chunkId);
    documents.set(documentId, item.document_name);
    previousDistance = item.distance;
    return {
      chunkId, documentId, documentName: item.document_name, chunkIndex: item.chunk_index as number,
      content: item.content, startChar, endChar, distance: item.distance, fusionScore, vectorRank, lexicalRank,
    };
  });
  return {
    strategy: expectedStrategy, metric,
    model: emptyProfile ? null : payload.model as string,
    dimensions: emptyProfile ? null : 1024,
    hits,
  };
}

export async function searchKnowledgeBase(
  workspaceId: string,
  knowledgeBaseId: string,
  query: string,
  signal: AbortSignal,
  strategy: SearchStrategy = "vector",
): Promise<KnowledgeSearchResult> {
  const normalizedWorkspaceId = strictUuid(workspaceId);
  const normalizedKnowledgeBaseId = strictUuid(knowledgeBaseId);
  if (!isSearchStrategy(strategy)) throw new ApiError("检索方式无效，请选择向量检索或混合检索。");
  const normalizedQuery = query.trim();
  let queryLength = 0;
  for (const _character of normalizedQuery) {
    queryLength += 1;
    if (queryLength > 2000) break;
  }
  if (queryLength < 1 || queryLength > 2000) throw new ApiError("请输入 1 到 2000 字符的检索问题。");
  if (signal.aborted) throw new SearchUnconfirmedError();
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, 330_000);
  let result: KnowledgeSearchResult;
  try {
    const response = await fetch(
      `/api/v1/workspaces/${normalizedWorkspaceId}/knowledge-bases/${normalizedKnowledgeBaseId}/search`,
      webRequestOptions({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: normalizedQuery, top_k: 3, strategy, rerank: false }),
      }, controller.signal),
    );
    if (controller.signal.aborted) throw new SearchUnconfirmedError();
    const rejectionMessages: Record<number, string> = {
      401: SESSION_EXPIRED,
      403: "检索请求被拒绝，请检查登录与浏览器来源。",
      404: "该知识库不存在或你已无权访问，请返回知识库列表。",
      409: "当前知识库向量模型配置不一致，无法完成本次检索；本响应不能确认是否已产生费用。",
      422: strategy === "hybrid"
        ? "检索问题、请求参数或混合检索处理量可能不符合要求（上限为 2000 个可检索文本块、100 万正文字符）；本响应不能确认是否已产生费用。"
        : "请核对检索问题与请求参数；本响应不能确认是否已产生费用。",
    };
    if (Object.hasOwn(rejectionMessages, response.status)) {
      throw new ApiError(rejectionMessages[response.status], response.status);
    }
    if (response.status !== 200) throw new SearchUnconfirmedError(response.status);
    const payload: unknown = await response.json();
    if (controller.signal.aborted) throw new SearchUnconfirmedError();
    try {
      result = parseKnowledgeSearch(payload, strategy);
    } catch {
      throw new SearchUnconfirmedError();
    }
  } catch (failure) {
    if (failure instanceof ApiError) throw failure;
    throw new SearchUnconfirmedError();
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
  try {
    const documents = new Map(result.hits.map((hit) => [hit.documentId, hit.documentName]));
    for (const [documentId, documentName] of documents) {
      if (signal.aborted) throw new SearchUnconfirmedError();
      const document = await getDocument(normalizedWorkspaceId, normalizedKnowledgeBaseId, documentId, signal);
      if (signal.aborted) throw new SearchUnconfirmedError();
      if (document.originalFilename !== documentName || document.status !== "ready") {
        throw new SearchUnconfirmedError();
      }
    }
    if (signal.aborted) throw new SearchUnconfirmedError();
    return result;
  } catch (failure) {
    if (signal.aborted) throw new SearchUnconfirmedError();
    if (failure instanceof ApiError && failure.status === 401) throw failure;
    throw new SearchUnconfirmedError(failure instanceof ApiError ? failure.status : null);
  }
}

export function parseKnowledgeAnswer(payload: unknown): KnowledgeAnswerResult {
  if (!isObject(payload) || typeof payload.answer !== "string" || !payload.answer.trim()
      || typeof payload.abstained !== "boolean" || !Array.isArray(payload.citations)
      || payload.citations.length > 20
      || (payload.abstained && payload.citations.length !== 0)
      || (!payload.abstained && payload.citations.length === 0)
      || (payload.reranking !== undefined && payload.reranking !== null)) {
    throw new AnswerUnconfirmedError();
  }
  let answerLength = 0;
  for (const _character of payload.answer) {
    answerLength += 1;
    if (answerLength > 4000) throw new AnswerUnconfirmedError();
  }
  const documents = new Map<string, string>();
  const chunks = new Map<string, { documentId: string; chunkIndex: number; quotes: Set<string> }>();
  const citations = payload.citations.map((item: unknown): AnswerCitation => {
    if (!isObject(item) || typeof item.document_name !== "string"
        || Array.from(item.document_name).length < 1 || Array.from(item.document_name).length > 255
        || !Number.isSafeInteger(item.chunk_index) || (item.chunk_index as number) < 1
        || typeof item.quote !== "string" || !item.quote.trim()
        || !Number.isSafeInteger(item.start_char) || !Number.isSafeInteger(item.end_char)) {
      throw new AnswerUnconfirmedError();
    }
    const chunkId = strictUuid(item.chunk_id);
    const documentId = strictUuid(item.document_id);
    const chunkIndex = item.chunk_index as number;
    const startChar = item.start_char as number;
    const endChar = item.end_char as number;
    if (startChar < 0 || endChar <= startChar
        || (documents.has(documentId) && documents.get(documentId) !== item.document_name)) {
      throw new AnswerUnconfirmedError();
    }
    let quoteLength = 0;
    for (const _character of item.quote) {
      quoteLength += 1;
      if (quoteLength > 1000) throw new AnswerUnconfirmedError();
    }
    if (endChar - startChar !== quoteLength) throw new AnswerUnconfirmedError();
    const previous = chunks.get(chunkId);
    if (previous && (previous.documentId !== documentId || previous.chunkIndex !== chunkIndex
        || previous.quotes.has(item.quote))) {
      throw new AnswerUnconfirmedError();
    }
    if (previous) previous.quotes.add(item.quote);
    else chunks.set(chunkId, { documentId, chunkIndex, quotes: new Set([item.quote]) });
    documents.set(documentId, item.document_name);
    return { chunkId, documentId, documentName: item.document_name, chunkIndex,
      quote: item.quote, startChar, endChar };
  });
  return { answer: payload.answer, abstained: payload.abstained, citations };
}

export async function answerKnowledgeBase(
  workspaceId: string,
  knowledgeBaseId: string,
  question: string,
  signal: AbortSignal,
): Promise<KnowledgeAnswerResult> {
  const normalizedWorkspaceId = strictUuid(workspaceId);
  const normalizedKnowledgeBaseId = strictUuid(knowledgeBaseId);
  const normalizedQuestion = question.trim();
  let questionLength = 0;
  for (const _character of normalizedQuestion) {
    questionLength += 1;
    if (questionLength > 2000) break;
  }
  if (questionLength < 1 || questionLength > 2000) throw new ApiError("请输入 1 到 2000 字符的问答问题。");
  if (signal.aborted) throw new AnswerUnconfirmedError();
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, 630_000);
  let result: KnowledgeAnswerResult;
  try {
    const response = await fetch(
      `/api/v1/workspaces/${normalizedWorkspaceId}/knowledge-bases/${normalizedKnowledgeBaseId}/answers`,
      webRequestOptions({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: normalizedQuestion, top_k: 3, strategy: "vector", rerank: false }),
      }, controller.signal),
    );
    if (controller.signal.aborted) throw new AnswerUnconfirmedError();
    const rejectionMessages: Record<number, string> = {
      401: SESSION_EXPIRED,
      403: "问答请求被拒绝，请检查登录与浏览器来源。",
      404: "该知识库不存在或你已无权访问，请返回知识库列表。",
      409: "当前知识库向量模型配置不一致，无法完成本次问答；本响应不能确认是否已产生费用。",
      422: "请核对问答问题、请求参数与上下文预算；本响应不能确认是否已产生费用。",
    };
    if (Object.hasOwn(rejectionMessages, response.status)) {
      throw new ApiError(rejectionMessages[response.status], response.status, responseTraceId(response));
    }
    if (response.status !== 200) throw new AnswerUnconfirmedError(response.status, responseTraceId(response));
    const payload: unknown = await response.json();
    if (controller.signal.aborted) throw new AnswerUnconfirmedError();
    try {
      result = parseKnowledgeAnswer(payload);
    } catch {
      throw new AnswerUnconfirmedError();
    }
  } catch (failure) {
    if (failure instanceof ApiError) throw failure;
    throw new AnswerUnconfirmedError();
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
  try {
    const documents = new Map(result.citations.map((citation) => [citation.documentId, citation.documentName]));
    for (const [documentId, documentName] of documents) {
      if (signal.aborted) throw new AnswerUnconfirmedError();
      const document = await getDocument(normalizedWorkspaceId, normalizedKnowledgeBaseId, documentId, signal);
      if (signal.aborted) throw new AnswerUnconfirmedError();
      if (document.originalFilename !== documentName || document.status !== "ready") {
        throw new AnswerUnconfirmedError();
      }
    }
    if (signal.aborted) throw new AnswerUnconfirmedError();
    return result;
  } catch (failure) {
    if (signal.aborted) throw new AnswerUnconfirmedError();
    if (failure instanceof ApiError && failure.status === 401) throw failure;
    throw new AnswerUnconfirmedError(failure instanceof ApiError ? failure.status : null,
      failure instanceof ApiError ? failure.traceId : null);
  }
}

export function validateUploadFile(file: File): string | null {
  const name = file.name.trim();
  if (!name || Array.from(name).length > 255 || /[/\\\u0000-\u001f\u007f]/.test(name)) {
    return "文件名无效，请选择名称不含路径或控制字符且不超过 255 字的文件。";
  }
  if (!/\.(txt|md|pdf)$/i.test(name)) return "仅支持 TXT、Markdown 和 PDF 文件。";
  if (!Number.isSafeInteger(file.size) || file.size < 1) return "文件不能为空。";
  if (file.size > 5 * 1024 * 1024) return "文件不能超过 5 MiB。";
  return null;
}

function parseUploadedDocument(
  payload: unknown,
  knowledgeBaseId: string,
  filename: string,
  byteSize: number,
  digest: string,
): UploadedDocument {
  if (!isObject(payload) || payload.original_filename !== filename
      || payload.byte_size !== byteSize || payload.sha256 !== digest
      || (payload.status !== "parsed" && payload.status !== "failed")
      || (payload.status === "parsed" && payload.error_code !== null)
      || (payload.status === "failed" && (typeof payload.error_code !== "string" || !payload.error_code))) {
    throw new UploadUnconfirmedError();
  }
  let id: string;
  let returnedKnowledgeBaseId: string;
  try {
    id = strictUuid(payload.id);
    returnedKnowledgeBaseId = strictUuid(payload.knowledge_base_id);
  } catch {
    throw new UploadUnconfirmedError();
  }
  if (returnedKnowledgeBaseId !== knowledgeBaseId) throw new UploadUnconfirmedError();
  const parseErrors: Record<string, string> = {
    EMPTY_TEXT: "文件中没有可解析的文字。",
    INVALID_UTF8: "文本文件不是有效的 UTF-8 编码。",
    STORAGE_READ_FAILED: "读取已保存文件失败。",
    PDF_PASSWORD_REQUIRED: "PDF 需要密码，暂不支持解析。",
    PDF_TOO_MANY_PAGES: "PDF 超过 100 页的解析上限。",
    PDF_TEXT_TOO_LONG: "PDF 提取文字超过解析上限。",
    PDF_INVALID: "PDF 格式无效或无法解析。",
    PDF_NO_TEXT: "PDF 中没有可提取文字，当前不支持 OCR。",
  };
  return {
    id, knowledgeBaseId: returnedKnowledgeBaseId, originalFilename: filename, status: payload.status,
    parseError: payload.status === "failed"
      ? (Object.hasOwn(parseErrors, payload.error_code as string)
        ? parseErrors[payload.error_code as string] : "文件解析失败，请检查文件内容。")
      : null,
  };
}

export async function uploadDocument(
  workspaceId: string,
  knowledgeBaseId: string,
  file: File,
  signal: AbortSignal,
): Promise<UploadedDocument> {
  const normalizedWorkspaceId = strictUuid(workspaceId);
  const normalizedKnowledgeBaseId = strictUuid(knowledgeBaseId);
  const validationError = validateUploadFile(file);
  if (validationError) throw new ApiError(validationError);
  const checkBeforeSending = () => {
    if (signal.aborted) throw new ApiError("请求已取消。");
  };
  checkBeforeSending();
  let digest: string;
  try {
    const bytes = await file.arrayBuffer();
    checkBeforeSending();
    if (bytes.byteLength !== file.size) throw new Error();
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    checkBeforeSending();
    digest = Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
  } catch {
    checkBeforeSending();
    throw new ApiError("无法读取所选文件，请重新选择后重试。");
  }
  checkBeforeSending();
  const body = new FormData();
  body.append("file", file, file.name);
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, 10_000);
  try {
    const response = await fetch(
      `/api/v1/workspaces/${normalizedWorkspaceId}/knowledge-bases/${normalizedKnowledgeBaseId}/documents`,
      webRequestOptions({ method: "POST", body }, controller.signal),
    );
    if (controller.signal.aborted) throw new UploadUnconfirmedError();
    const rejectionMessages: Record<number, string> = {
      401: SESSION_EXPIRED,
      403: "上传请求被拒绝，请检查登录与浏览器来源后重试。",
      404: "该知识库不存在或你已无权访问，请返回知识库列表。",
      413: "文件超过后端大小上限，请选择更小的文件。",
      415: "后端仅支持 TXT、Markdown 和 PDF 文件。",
      422: "文件未被接受，请检查文件名、文件内容和大小后重试。",
    };
    if (Object.hasOwn(rejectionMessages, response.status)) {
      throw new ApiError(rejectionMessages[response.status], response.status);
    }
    if (response.status !== 201) throw new UploadUnconfirmedError(response.status);
    const payload: unknown = await response.json();
    if (controller.signal.aborted) throw new UploadUnconfirmedError();
    return parseUploadedDocument(payload, normalizedKnowledgeBaseId, file.name.trim(), file.size, digest);
  } catch (failure) {
    if (failure instanceof ApiError) throw failure;
    throw new UploadUnconfirmedError();
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
}
