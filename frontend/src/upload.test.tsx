import { createHash } from 'node:crypto';

import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';

const USER = '00000000-0000-0000-0000-000000000001';
const SPACE_A = '00000000-0000-0000-0000-00000000000a';
const SPACE_B = '00000000-0000-0000-0000-00000000000b';
const KB_A = '00000000-0000-0000-0000-000000000101';
const KB_B = '00000000-0000-0000-0000-000000000102';
const NEW_DOCUMENT = '00000000-0000-0000-0000-000000000399';
const RESTORE = '/api/v1/auth/web/session';
const LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces';
const KNOWLEDGE_A = `${SPACES}/${SPACE_A}/knowledge-bases`;
const KNOWLEDGE_B = `${SPACES}/${SPACE_B}/knowledge-bases`;
const DOCUMENTS_A = `${KNOWLEDGE_A}/${KB_A}/documents`;
const DOCUMENTS_B = `${KNOWLEDGE_B}/${KB_B}/documents`;
const NEW_DOCUMENT_PATH = `${DOCUMENTS_A}/${NEW_DOCUMENT}`;
const NEW_CHUNKS_PATH = `${NEW_DOCUMENT_PATH}/chunks`;
const NEW_CHUNK = '00000000-0000-0000-0000-000000000499';
const LATE_CHUNK_TEXT = '迟到文本块不得恢复到新页面。';
const PRIVATE_RESPONSE = 'PRIVATE_UPLOAD_RESPONSE_MUST_NOT_BE_DISPLAYED';
const UNCONFIRMED = '上传结果未确认；未自动重试。请先核对知识库中的文档记录，再决定是否再次上传。';
const CONTENT = '仅供模拟测试的上传内容。\n';
const BYTES = new TextEncoder().encode(CONTENT);
const SHA256 = createHash('sha256').update(BYTES).digest('hex');
const FILENAME = '上传样例.md';

type Route = (request: Request) => Response | Promise<Response> | undefined;
type Captured = { request: Request; body: BodyInit | null | undefined };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function sessionResponse(expiresAt = Date.now() / 1000 + 1800): Response {
  return json({ user: { id: USER, username: 'demo_a' }, expires_at: expiresAt });
}

function documentRow(index: number, filename = `原有文档${index}.txt`, knowledgeBaseId = KB_A) {
  return {
    id: `00000000-0000-0000-0000-${(0x300 + index).toString(16).padStart(12, '0')}`,
    knowledge_base_id: knowledgeBaseId,
    original_filename: filename,
    status: 'ready',
  };
}

function uploaded(status: 'parsed' | 'failed' = 'parsed', changes: Record<string, unknown> = {}) {
  return {
    id: NEW_DOCUMENT,
    knowledge_base_id: KB_A,
    original_filename: FILENAME,
    byte_size: BYTES.byteLength,
    sha256: SHA256,
    status,
    error_code: status === 'parsed' ? null : 'INVALID_UTF8',
    ...changes,
  };
}

function uploadedDetail(changes: Record<string, unknown> = {}) {
  return { id: NEW_DOCUMENT, knowledge_base_id: KB_A, original_filename: FILENAME, status: 'parsed', ...changes };
}

function uploadedChunks() {
  return [{ id: NEW_CHUNK, document_id: NEW_DOCUMENT, chunk_index: 1,
    content: LATE_CHUNK_TEXT, start_char: 0, end_char: Array.from(LATE_CHUNK_TEXT).length }];
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function pathOf(request: Request) { return new URL(request.url).pathname; }
function uploads(captured: Captured[]) {
  return captured.filter(({ request }) => request.method === 'POST' && pathOf(request).endsWith('/documents'));
}
function reads(captured: Captured[]) {
  return captured.filter(({ request }) => request.method === 'GET' && pathOf(request).endsWith('/documents'));
}

// 保留原始 FormData 以检查字段；Request 仅用于请求头、范围和取消信号。
// 故意允许取消后的模拟请求迟到，验证应用不会接受这些结果。
function installApi(route: Route = () => undefined): Captured[] {
  const captured: Captured[] = [];
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const { body, ...options } = init ?? {};
    const request = input instanceof Request
      ? new Request(input, options)
      : new Request(new URL(String(input), 'http://localhost'), options);
    captured.push({ request, body });
    const response = route(request);
    if (response !== undefined) return response;
    switch (pathOf(request)) {
      case RESTORE:
      case '/api/v1/auth/web/login': return sessionResponse();
      case LOGOUT: return new Response(null, { status: 204 });
      case SPACES: return json([{ id: SPACE_A, name: '甲空间' }, { id: SPACE_B, name: '乙空间' }]);
      case KNOWLEDGE_A: return json([{ id: KB_A, workspace_id: SPACE_A, name: '甲知识库' }]);
      case KNOWLEDGE_B: return json([{ id: KB_B, workspace_id: SPACE_B, name: '乙知识库' }]);
      case DOCUMENTS_A:
        if (request.method === 'POST') return json(uploaded(), 201);
        return json([documentRow(1)]);
      case DOCUMENTS_B: return json([documentRow(2, '乙空间文档.txt', KB_B)]);
      case NEW_DOCUMENT_PATH: return json(uploadedDetail());
      case NEW_CHUNKS_PATH: return json([]);
      default: throw new Error('Unexpected mocked endpoint');
    }
  }));
  return captured;
}

function file(name = FILENAME, bytes = BYTES): File {
  const selected = new File([Uint8Array.from(bytes)], name, { type: 'text/plain' });
  // jsdom File 没有 arrayBuffer；桥返回该 fixture 的真实 UTF-8 字节。
  Object.defineProperty(selected, 'arrayBuffer', { value: async () => Uint8Array.from(bytes).buffer });
  return selected;
}

async function advance(milliseconds = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
}

async function click(element: HTMLElement) {
  await act(async () => {
    fireEvent.click(element);
    await vi.advanceTimersByTimeAsync(0);
  });
}

async function selectFile(selected = file()) {
  await act(async () => {
    fireEvent.change(screen.getByLabelText('选择文档'), { target: { files: [selected] } });
    await vi.advanceTimersByTimeAsync(0);
  });
}

async function openDocuments() {
  await advance();
  await click(screen.getByRole('button', { name: '进入甲空间' }));
  await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
}

function result() { return screen.getByRole('region', { name: '本次上传结果' }); }

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
  // 保留真实 SHA-256 计算，消除线程池与 fakeTimers 的竞态。
  vi.stubGlobal('crypto', { subtle: { digest: vi.fn(async (algorithm: AlgorithmIdentifier, data: BufferSource) => {
    expect(algorithm).toBe('SHA-256');
    const bytes = ArrayBuffer.isView(data)
      ? new Uint8Array(data.buffer, data.byteOffset, data.byteLength)
      : new Uint8Array(data);
    return Uint8Array.from(createHash('sha256').update(bytes).digest()).buffer;
  }) } });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('网页显式上传单份文档', () => {
  it('选择文件不上传，显式点击才向当前库发送一次 multipart，上传期间阻止重复提交', async () => {
    const pending = deferred<Response>();
    const captured = installApi(request => {
      if (request.method === 'POST' && pathOf(request) === DOCUMENTS_A) return pending.promise;
    });
    render(<App />);
    await openDocuments();
    expect((screen.getByRole('button', { name: '上传' }) as HTMLButtonElement).disabled).toBe(true);
    await selectFile();
    expect(uploads(captured)).toHaveLength(0);
    expect(screen.getByText(FILENAME)).toBeTruthy();

    await click(screen.getByRole('button', { name: '上传' }));
    const [upload] = uploads(captured);
    expect(uploads(captured)).toHaveLength(1);
    expect(pathOf(upload.request)).toBe(DOCUMENTS_A);
    expect(new URL(upload.request.url).search).toBe('');
    expect(upload.request.credentials).toBe('same-origin');
    expect(upload.request.headers.get('X-EAAP-Web')).toBe('1');
    expect(upload.request.headers.has('Authorization')).toBe(false);
    expect(upload.request.headers.has('Content-Type')).toBe(false);
    expect(upload.body).toBeInstanceOf(FormData);
    const multipart = upload.body as FormData;
    expect(Array.from(multipart.keys())).toEqual(['file']);
    const sent = multipart.get('file') as File;
    expect(sent.name).toBe(FILENAME);
    expect(sent.size).toBe(BYTES.byteLength);
    expect(crypto.subtle.digest).toHaveBeenCalledOnce();
    expect((screen.getByLabelText('选择文档') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '刷新列表' }) as HTMLButtonElement).disabled).toBe(true);
    const submit = screen.getByRole('button', { name: '上传' }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    await click(submit);
    expect(uploads(captured)).toHaveLength(1);
    expect(reads(captured)).toHaveLength(1);

    await act(async () => { pending.resolve(json(uploaded(), 201)); await vi.advanceTimersByTimeAsync(0); });
    expect(within(result()).getByText(FILENAME)).toBeTruthy();
    expect(reads(captured)).toHaveLength(2);
    expect(uploads(captured)).toHaveLength(1);
  });

  it.each(['parsed', 'failed'] as const)('201 %s 显示已创建记录的解析结果，刷新文档但不变为可检索', async status => {
    const captured = installApi(request => {
      if (request.method === 'POST' && pathOf(request) === DOCUMENTS_A) return json(uploaded(status), 201);
    });
    render(<App />);
    await openDocuments();
    await selectFile();
    await click(screen.getByRole('button', { name: '上传' }));

    const card = result();
    expect(card.textContent).toContain('文档记录已创建');
    expect(card.textContent).toContain(status === 'parsed' ? '已解析' : '解析失败');
    if (status === 'parsed') expect(card.textContent).toContain('尚未可检索');
    else expect(card.textContent).toContain('UTF-8');
    expect(card.textContent).not.toContain(NEW_DOCUMENT);
    expect(card.textContent).not.toContain(SHA256);
    expect(uploads(captured)).toHaveLength(1);
    expect(reads(captured)).toHaveLength(2);
    expect((screen.getByRole('button', { name: '上传' }) as HTMLButtonElement).disabled).toBe(true);
    expect(document.body.textContent).not.toContain(UNCONFIRMED);
  });

  it('第一页满二十条时本次结果独立保留，不拼入列表或改变当前页计数', async () => {
    const rows = Array.from({ length: 20 }, (_, index) => documentRow(index + 1));
    const captured = installApi(request => {
      if (request.method === 'GET' && pathOf(request) === DOCUMENTS_A) return json(rows);
    });
    render(<App />);
    await openDocuments();
    await selectFile();
    await click(screen.getByRole('button', { name: '上传' }));

    expect(within(result()).getByText(FILENAME)).toBeTruthy();
    expect(screen.getByText('当前页 · 20 份文档 · 20 份可检索 · 全部处理状态')).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 2, name: FILENAME })).toBeNull();
    expect(screen.getByRole('heading', { level: 2, name: '原有文档20.txt' })).toBeTruthy();
    await click(screen.getByRole('button', { name: '刷新列表' }));
    expect(within(result()).getByText(FILENAME)).toBeTruthy();
    expect(reads(captured)).toHaveLength(3);
    expect(uploads(captured)).toHaveLength(1);
  });

  it('上传已确认后列表刷新失败，仍保留本次成功且仅重试 GET', async () => {
    let documentReads = 0;
    const captured = installApi(request => {
      if (request.method === 'GET' && pathOf(request) === DOCUMENTS_A && ++documentReads === 2) {
        return json({ detail: PRIVATE_RESPONSE }, 503);
      }
    });
    render(<App />);
    await openDocuments();
    await selectFile();
    await click(screen.getByRole('button', { name: '上传' }));
    expect(result().textContent).toContain('已解析');
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(document.body.textContent).not.toContain(UNCONFIRMED);
    expect(document.body.textContent).not.toContain(PRIVATE_RESPONSE);

    await click(screen.getByRole('button', { name: '重试加载' }));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(result().textContent).toContain('已解析');
    expect(uploads(captured)).toHaveLength(1);
    expect(reads(captured)).toHaveLength(3);
  });

  it.each([
    ['不支持的后缀', () => file('上传样例.exe')],
    ['空文件', () => file('空文件.txt', new Uint8Array())],
    ['超过五 MiB', () => file('过大.pdf', new Uint8Array(5 * 1024 * 1024 + 1))],
  ] as const)('选择%s不会发送请求', async (_description, makeFile) => {
    const captured = installApi();
    render(<App />);
    await openDocuments();
    await selectFile(makeFile());
    const submit = screen.getByRole('button', { name: '上传' });
    await click(submit);
    expect(uploads(captured)).toHaveLength(0);
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.queryByRole('region', { name: '本次上传结果' })).toBeNull();
  });

  it.each(['network', 'server', 'mismatched-hash'] as const)('%s 只报告上传未确认，刷新核对不自动重传', async failure => {
    const captured = installApi(request => {
      if (request.method !== 'POST' || pathOf(request) !== DOCUMENTS_A) return;
      if (failure === 'network') return Promise.reject(new TypeError(PRIVATE_RESPONSE));
      if (failure === 'server') return json({ detail: PRIVATE_RESPONSE }, 503);
      return json(uploaded('parsed', { sha256: '0'.repeat(64) }), 201);
    });
    render(<App />);
    await openDocuments();
    await selectFile();
    await click(screen.getByRole('button', { name: '上传' }));

    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(result().textContent).not.toContain('已解析');
    expect(document.body.textContent).not.toContain(PRIVATE_RESPONSE);
    expect((screen.getByRole('button', { name: '上传' }) as HTMLButtonElement).disabled).toBe(true);
    await advance(30_000);
    expect(uploads(captured)).toHaveLength(1);
    await click(screen.getByRole('button', { name: '刷新列表' }));
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(reads(captured)).toHaveLength(2);
    expect(uploads(captured)).toHaveLength(1);
  });

  it('停止等待后显示未确认，隔离迟到201并要求重新选择文件', async () => {
    const pending = deferred<Response>();
    const captured = installApi(request => {
      if (request.method === 'POST' && pathOf(request) === DOCUMENTS_A) return pending.promise;
    });
    render(<App />);
    await openDocuments();
    await selectFile();
    await click(screen.getByRole('button', { name: '上传' }));
    const [{ request }] = uploads(captured);
    await click(screen.getByRole('button', { name: '停止等待上传结果' }));
    expect(request.signal.aborted).toBe(true);
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect((screen.getByRole('button', { name: '上传' }) as HTMLButtonElement).disabled).toBe(true);
    expect(reads(captured)).toHaveLength(1);

    await act(async () => { pending.resolve(json(uploaded(), 201)); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(result().textContent).not.toContain('已解析');
    expect(reads(captured)).toHaveLength(1);
    expect(uploads(captured)).toHaveLength(1);
    await selectFile();
    expect((screen.getByRole('button', { name: '上传' }) as HTMLButtonElement).disabled).toBe(false);
    expect(uploads(captured)).toHaveLength(1);
  });

  it('等待十秒超时后提示未确认，保留会话并且不自动再次上传', async () => {
    const pending = deferred<Response>();
    const captured = installApi(request => {
      if (request.method !== 'POST' || pathOf(request) !== DOCUMENTS_A) return;
      request.signal.addEventListener('abort', () => {
        pending.reject(new DOMException('Aborted mock request', 'AbortError'));
      }, { once: true });
      return pending.promise;
    });
    render(<App />);
    await openDocuments();
    await selectFile();
    await click(screen.getByRole('button', { name: '上传' }));
    const [{ request }] = uploads(captured);
    await advance(9999);
    expect(screen.getByRole('button', { name: '停止等待上传结果' })).toBeTruthy();
    expect(request.signal.aborted).toBe(false);
    await advance(1);
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(request.signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '停止等待上传结果' })).toBeNull();
    await advance(30_000);
    expect(uploads(captured)).toHaveLength(1);
    expect(reads(captured)).toHaveLength(1);
  });

  it.each([201, 404])('返回并进入其他空间后，不接收旧上传的迟到%i', async lateStatus => {
    const pending = deferred<Response>();
    const captured = installApi(request => {
      if (request.method === 'POST' && pathOf(request) === DOCUMENTS_A) return pending.promise;
    });
    render(<App />);
    await openDocuments();
    await selectFile();
    await click(screen.getByRole('button', { name: '上传' }));
    const [{ request }] = uploads(captured);
    await click(screen.getByRole('button', { name: '返回知识库' }));
    await click(screen.getByRole('button', { name: '返回工作空间' }));
    await click(screen.getByRole('button', { name: '进入乙空间' }));
    await click(screen.getByRole('button', { name: '查看乙知识库的文档' }));
    expect(request.signal.aborted).toBe(true);

    await act(async () => {
      pending.resolve(lateStatus === 201 ? json(uploaded(), 201) : json({ detail: PRIVATE_RESPONSE }, lateStatus));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { level: 2, name: '乙空间文档.txt' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: '本次上传结果' })).toBeNull();
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(document.body.textContent).not.toContain(FILENAME);
    expect(reads(captured).map(({ request: read }) => pathOf(read))).toEqual([DOCUMENTS_A, DOCUMENTS_B]);
    expect(uploads(captured)).toHaveLength(1);
  });

  it('上传401清除身份、文件与结果，再登录从工作空间开始', async () => {
    const captured = installApi(request => {
      if (request.method === 'POST' && pathOf(request) === DOCUMENTS_A) return json({ detail: PRIVATE_RESPONSE }, 401);
    });
    render(<App />);
    await openDocuments();
    await selectFile();
    await click(screen.getByRole('button', { name: '上传' }));
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByLabelText('选择文档')).toBeNull();
    expect(screen.queryByRole('region', { name: '本次上传结果' })).toBeNull();
    expect(document.body.textContent).not.toContain(FILENAME);
    expect(document.body.textContent).not.toContain(PRIVATE_RESPONSE);
    await act(async () => {
      fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'demo_a' } });
      fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'fake-upload-test-password' } });
    });
    await click(screen.getByRole('button', { name: '登录' }));
    expect(screen.getByRole('heading', { name: '你的工作空间' })).toBeTruthy();
    expect(screen.queryByLabelText('选择文档')).toBeNull();
    expect(uploads(captured)).toHaveLength(1);
  });

  it('退出中断上传但退出未确认时，返回原文档页仍提示上传未确认', async () => {
    const pending = deferred<Response>();
    const captured = installApi(request => {
      if (request.method === 'POST' && pathOf(request) === DOCUMENTS_A) return pending.promise;
      if (pathOf(request) === LOGOUT) return Promise.reject(new TypeError(PRIVATE_RESPONSE));
    });
    render(<App />);
    await openDocuments();
    await selectFile();
    await click(screen.getByRole('button', { name: '上传' }));
    const [{ request }] = uploads(captured);
    await click(screen.getByRole('button', { name: '退出登录' }));
    expect(request.signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(document.body.textContent).toContain('退出尚未确认');
    expect(document.body.textContent).not.toContain(PRIVATE_RESPONSE);
    await act(async () => { pending.resolve(json(uploaded(), 201)); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(result().textContent).not.toContain('已解析');
    expect(uploads(captured)).toHaveLength(1);
    expect(reads(captured)).toHaveLength(1);
  });

  it('上传不续期，原会话到期清除页面并隔离迟到创建结果', async () => {
    const pending = deferred<Response>();
    const captured = installApi(request => {
      if (pathOf(request) === RESTORE) return sessionResponse(Date.now() / 1000 + 2);
      if (request.method === 'POST' && pathOf(request) === DOCUMENTS_A) return pending.promise;
    });
    render(<App />);
    await openDocuments();
    await selectFile();
    await click(screen.getByRole('button', { name: '上传' }));
    const [{ request }] = uploads(captured);
    await advance(1999);
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    await advance(1);
    expect(request.signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    await act(async () => { pending.resolve(json(uploaded(), 201)); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByLabelText('选择文档')).toBeNull();
    expect(screen.queryByRole('region', { name: '本次上传结果' })).toBeNull();
    expect(document.body.textContent).not.toContain(FILENAME);
    expect(reads(captured)).toHaveLength(1);
    expect(uploads(captured)).toHaveLength(1);
    expect(captured.filter(({ request: sent }) => pathOf(sent) === RESTORE)).toHaveLength(1);
  });
});

function continueButton() {
  return screen.getByRole('button', { name: '继续处理这份文档' }) as HTMLButtonElement;
}

async function uploadOneDocument() {
  await selectFile();
  await click(screen.getByRole('button', { name: '上传' }));
}

function processingReads(captured: Captured[]) {
  return captured.filter(({ request }) => request.method === 'GET'
    && [NEW_DOCUMENT_PATH, NEW_CHUNKS_PATH].includes(pathOf(request)));
}

function expectOnlyOriginalUpload(captured: Captured[]) {
  expect(uploads(captured)).toHaveLength(1);
  expect(captured.filter(({ request }) => request.method !== 'GET' && pathOf(request) !== LOGOUT)
    .map(({ request }) => [request.method, pathOf(request)])).toEqual([['POST', DOCUMENTS_A]]);
}

describe('从已确认上传结果继续处理文档', () => {
  it('第一页只有20条旧记录时仍按本次上传ID进入，先读最新详情再读块，不自动切块或向量化', async () => {
    const rows = Array.from({ length: 20 }, (_, index) => documentRow(index + 1));
    const captured = installApi(request => request.method === 'GET' && pathOf(request) === DOCUMENTS_A ? json(rows) : undefined);
    render(<App />); await openDocuments();
    expect(screen.queryByRole('button', { name: '继续处理这份文档' })).toBeNull();
    await uploadOneDocument();
    expect(screen.queryByRole('heading', { level: 2, name: FILENAME })).toBeNull();
    expect(screen.getByText('当前页 · 20 份文档 · 20 份可检索 · 全部处理状态')).toBeTruthy();
    expect(continueButton().disabled).toBe(false);
    expect(processingReads(captured)).toHaveLength(0);
    const button = continueButton();
    await act(async () => {
      fireEvent.click(button);
      fireEvent.click(button);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { level: 1, name: '文档处理' })).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: FILENAME })).toBeTruthy();
    expect(screen.getByText('当前没有文本块')).toBeTruthy();
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: '生成向量' }) as HTMLButtonElement).disabled).toBe(true);
    expect(processingReads(captured).map(({ request }) => pathOf(request))).toEqual([NEW_DOCUMENT_PATH, NEW_CHUNKS_PATH]);
    for (const { request, body } of processingReads(captured)) {
      expect(new URL(request.url).search).toBe(''); expect(body).toBeUndefined();
      expect(request.credentials).toBe('same-origin'); expect(request.headers.get('X-EAAP-Web')).toBe('1');
      expect(request.headers.has('Authorization')).toBe(false);
    }
    expect(screen.queryByRole('region', { name: '本次上传结果' })).toBeNull();
    const count = captured.length; await advance(30_000); expect(captured).toHaveLength(count);
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(screen.queryByRole('region', { name: '本次上传结果' })).toBeNull();
    expect(screen.queryByRole('button', { name: '继续处理这份文档' })).toBeNull();
    expectOnlyOriginalUpload(captured);
  });

  it('上传确认后列表刷新失败仍能继续处理，但不解锁上传文件输入', async () => {
    let listReads = 0;
    const captured = installApi(request => request.method === 'GET' && pathOf(request) === DOCUMENTS_A
      && ++listReads === 2 ? json({ detail: PRIVATE_RESPONSE }, 503) : undefined);
    render(<App />); await openDocuments(); await uploadOneDocument();
    expect(result().textContent).toContain('已解析'); expect(screen.getByRole('alert')).toBeTruthy();
    expect((screen.getByLabelText('选择文档') as HTMLInputElement).disabled).toBe(true);
    expect(continueButton().disabled).toBe(false);
    await click(continueButton());
    expect(screen.getByRole('heading', { level: 1, name: '文档处理' })).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('region', { name: '本次上传结果' })).toBeNull();
    expect(document.body.textContent).not.toContain(PRIVATE_RESPONSE);
    expect(processingReads(captured).map(({ request }) => pathOf(request))).toEqual([NEW_DOCUMENT_PATH, NEW_CHUNKS_PATH]);
    expect(reads(captured)).toHaveLength(2); expectOnlyOriginalUpload(captured);
  });

  it('上传后的列表GET仍等待时显示禁用入口，读取结束前点击不能进入或重复上传', async () => {
    const pending = deferred<Response>(); let listReads = 0;
    const captured = installApi(request => request.method === 'GET' && pathOf(request) === DOCUMENTS_A
      && ++listReads === 2 ? pending.promise : undefined);
    render(<App />); await openDocuments(); await uploadOneDocument();
    expect(result().textContent).toContain('已解析'); expect(continueButton().disabled).toBe(true);
    await click(continueButton());
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(processingReads(captured)).toHaveLength(0); expectOnlyOriginalUpload(captured);
    await act(async () => { pending.resolve(json([documentRow(1)])); await vi.advanceTimersByTimeAsync(0); });
    expect(continueButton().disabled).toBe(false); expect(processingReads(captured)).toHaveLength(0);
    await click(continueButton());
    expect(processingReads(captured).map(({ request }) => pathOf(request))).toEqual([NEW_DOCUMENT_PATH, NEW_CHUNKS_PATH]);
    expectOnlyOriginalUpload(captured);
  });

  it.each(['failed', 'unconfirmed', 'rejected'] as const)('%s上传结果不提供继续处理入口', async outcome => {
    const captured = installApi(request => {
      if (request.method !== 'POST' || pathOf(request) !== DOCUMENTS_A) return undefined;
      return outcome === 'failed' ? json(uploaded('failed'), 201)
        : json({ detail: PRIVATE_RESPONSE }, outcome === 'unconfirmed' ? 503 : 422);
    });
    render(<App />); await openDocuments(); await uploadOneDocument();
    expect(result()).toBeTruthy();
    expect(screen.queryByRole('button', { name: '继续处理这份文档' })).toBeNull();
    expect(processingReads(captured)).toHaveLength(0);
    expect(document.body.textContent).not.toContain(PRIVATE_RESPONSE);
    expectOnlyOriginalUpload(captured);
  });

  it.each([404, 401])('继续处理的详情GET返回%s时不借上传快照开放切块或读取块', async status => {
    const captured = installApi(request => pathOf(request) === NEW_DOCUMENT_PATH ? json({ detail: PRIVATE_RESPONSE }, status) : undefined);
    render(<App />); await openDocuments(); await uploadOneDocument(); await click(continueButton());
    expect(processingReads(captured).map(({ request }) => pathOf(request))).toEqual([NEW_DOCUMENT_PATH]);
    expect(screen.queryByRole('region', { name: '本次上传结果' })).toBeNull();
    expect(document.body.textContent).not.toContain(PRIVATE_RESPONSE);
    if (status === 401) {
      expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
      expect(document.body.textContent).not.toContain(FILENAME);
    } else {
      expect(screen.getByRole('heading', { level: 1, name: '文档处理' })).toBeTruthy();
      expect(screen.getByRole('alert')).toBeTruthy();
      expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByRole('button', { name: '生成向量' }) as HTMLButtonElement).disabled).toBe(true);
    }
    expectOnlyOriginalUpload(captured);
  });

  it('详情已确认但块GET失败时禁止处理，手动刷新只补GET且成功后才允许切块', async () => {
    let chunkReads = 0;
    const captured = installApi(request => pathOf(request) === NEW_CHUNKS_PATH && ++chunkReads === 1
      ? json({ detail: PRIVATE_RESPONSE }, 503) : undefined);
    render(<App />); await openDocuments(); await uploadOneDocument(); await click(continueButton());
    expect(screen.getByRole('heading', { level: 1, name: '文档处理' })).toBeTruthy();
    expect(screen.getByRole('alert')).toBeTruthy();
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '生成向量' }) as HTMLButtonElement).disabled).toBe(true);
    expect(document.body.textContent).not.toContain(PRIVATE_RESPONSE);
    await click(screen.getByRole('button', { name: '刷新文档与切块' }));
    expect(screen.queryByRole('alert')).toBeNull();
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(false);
    expect(processingReads(captured).map(({ request }) => pathOf(request))).toEqual([
      NEW_DOCUMENT_PATH, NEW_CHUNKS_PATH, NEW_DOCUMENT_PATH, NEW_CHUNKS_PATH,
    ]);
    expectOnlyOriginalUpload(captured);
  });

  it.each([
    ['detail', 'back'], ['detail', 'logout'], ['detail', 'expiry'],
    ['chunks', 'back'], ['chunks', 'logout'], ['chunks', 'expiry'],
  ] as const)('继续处理等待%s时%s取消读取，迟到成功不能恢复文件正文或追加请求', async (stage, action) => {
    const pending = deferred<Response>();
    const awaitedPath = stage === 'detail' ? NEW_DOCUMENT_PATH : NEW_CHUNKS_PATH;
    const captured = installApi(request => {
      if (pathOf(request) === RESTORE && action === 'expiry') return sessionResponse(Date.now() / 1000 + 2);
      if (pathOf(request) === awaitedPath) return pending.promise;
    });
    render(<App />); await openDocuments(); await uploadOneDocument(); await click(continueButton());
    const awaited = captured.find(({ request }) => pathOf(request) === awaitedPath)!.request;
    if (action === 'back') await click(screen.getByRole('button', { name: '返回文档列表' }));
    else if (action === 'logout') await click(screen.getByRole('button', { name: '退出登录' }));
    else await advance(2000);
    expect(awaited.signal.aborted).toBe(true);
    const count = captured.length;
    await act(async () => {
      pending.resolve(json(stage === 'detail' ? uploadedDetail() : uploadedChunks()));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(captured).toHaveLength(count);
    expect(screen.getByRole('heading', { name: action === 'back' ? '文档' : '登录工作空间' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: '本次上传结果' })).toBeNull();
    expect(screen.queryByRole('button', { name: '继续处理这份文档' })).toBeNull();
    expect(document.body.textContent).not.toContain(FILENAME);
    expect(document.body.textContent).not.toContain(LATE_CHUNK_TEXT);
    expect(processingReads(captured).map(({ request }) => pathOf(request))).toEqual(stage === 'detail'
      ? [NEW_DOCUMENT_PATH] : [NEW_DOCUMENT_PATH, NEW_CHUNKS_PATH]);
    expectOnlyOriginalUpload(captured);
  });
});
