import { createHash, webcrypto } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError, UPLOAD_UNCONFIRMED_MESSAGE, UploadUnconfirmedError, uploadDocument, validateUploadFile,
} from './api';

const WORKSPACE_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const KNOWLEDGE_BASE_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const DOCUMENT_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const UPLOAD = `/api/v1/workspaces/${WORKSPACE_ID}/knowledge-bases/${KNOWLEDGE_BASE_ID}/documents`;
const PRIVATE = 'PRIVATE-UNTRUSTED-DETAIL';
const CONTENT = '企业知识上传测试\n正文 😀';
const DIGEST = createHash('sha256').update(CONTENT).digest('hex');
const BYTE_SIZE = new TextEncoder().encode(CONTENT).byteLength;
const PARSED = {
  id: DOCUMENT_ID, knowledge_base_id: KNOWLEDGE_BASE_ID,
  original_filename: '知识.MD', byte_size: BYTE_SIZE, sha256: DIGEST, status: 'parsed', error_code: null,
};
const fetchMock = vi.fn<typeof fetch>();

function file(name = '知识.MD', content: BlobPart = CONTENT): File {
  return new File([content], name, { type: 'text/markdown' });
}

function readFile(source: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(source);
  });
}

function jsonResponse(payload: unknown, status = 201): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
}

async function rejection(action: Promise<unknown>): Promise<ApiError> {
  const failure: unknown = await action.then(() => {
    throw new Error('Expected API rejection');
  }, (error: unknown) => error);
  expect(failure).toBeInstanceOf(ApiError);
  expect(String(failure)).not.toContain(PRIVATE);
  return failure as ApiError;
}

async function expectUnconfirmed(action: Promise<unknown>): Promise<void> {
  expect(await rejection(action)).toBeInstanceOf(UploadUnconfirmedError);
  await expect(action).rejects.toMatchObject({ message: UPLOAD_UNCONFIRMED_MESSAGE });
}

function waitForFetch(): Promise<void> {
  return new Promise((resolve) => {
    fetchMock.mockImplementationOnce((_path, options) => {
      resolve();
      return new Promise<Response>((_done, reject) => {
        options?.signal?.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
      });
    });
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  // jsdom File lacks arrayBuffer; read its actual bytes through the browser FileReader API.
  vi.stubGlobal('crypto', webcrypto);
  Object.defineProperty(File.prototype, 'arrayBuffer', {
    configurable: true, value(this: File) { return readFile(this); },
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(File.prototype, 'arrayBuffer');
});

describe('single-file upload API boundary', () => {
  it('sends actual multipart file bytes to the selected scope and verifies the returned digest', async () => {
    const selected = file('  知识.MD  ');
    fetchMock.mockResolvedValueOnce(jsonResponse({
      ...PARSED, id: DOCUMENT_ID.toUpperCase(), knowledge_base_id: KNOWLEDGE_BASE_ID.toUpperCase(),
      storage_key: PRIVATE, extracted_text: PRIVATE,
    }));
    expect(await uploadDocument(WORKSPACE_ID.toUpperCase(), KNOWLEDGE_BASE_ID.toUpperCase(), selected,
      new AbortController().signal)).toEqual({
      id: DOCUMENT_ID, knowledgeBaseId: KNOWLEDGE_BASE_ID, originalFilename: '知识.MD',
      status: 'parsed', parseError: null,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe(UPLOAD);
    expect(options).toMatchObject({
      method: 'POST', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
    });
    const headers = new Headers(options?.headers);
    expect(headers.get('X-EAAP-Web')).toBe('1');
    for (const key of ['Content-Type', 'Authorization', 'Cookie']) expect(headers.has(key)).toBe(false);
    expect(options?.signal).toBeInstanceOf(AbortSignal);
    expect(options?.body).toBeInstanceOf(FormData);
    const body = options?.body as FormData;
    expect(Array.from(body.keys())).toEqual(['file']);
    const sent = body.get('file') as File;
    expect(sent.name).toBe(selected.name);
    expect(Array.from(new Uint8Array(await readFile(sent)))).toEqual(Array.from(new TextEncoder().encode(CONTENT)));
  });

  it.each([
    ['EMPTY_TEXT', '文件中没有可解析的文字。'],
    ['INVALID_UTF8', '文本文件不是有效的 UTF-8 编码。'],
    ['STORAGE_READ_FAILED', '读取已保存文件失败。'],
    ['PDF_PASSWORD_REQUIRED', 'PDF 需要密码，暂不支持解析。'],
    ['PDF_TOO_MANY_PAGES', 'PDF 超过 100 页的解析上限。'],
    ['PDF_TEXT_TOO_LONG', 'PDF 提取文字超过解析上限。'],
    ['PDF_INVALID', 'PDF 格式无效或无法解析。'],
    ['PDF_NO_TEXT', 'PDF 中没有可提取文字，当前不支持 OCR。'],
    [PRIVATE, '文件解析失败，请检查文件内容。'],
    ['__proto__', '文件解析失败，请检查文件内容。'],
  ])('accepts a created failed document and maps %s to safe text', async (code, message) => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...PARSED, status: 'failed', error_code: code }));
    expect(await uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, file(), new AbortController().signal))
      .toEqual({
        id: DOCUMENT_ID, knowledgeBaseId: KNOWLEDGE_BASE_ID, originalFilename: '知识.MD',
        status: 'failed', parseError: message,
      });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('rejects invalid filename, type, empty and oversized files before any request', async () => {
    const invalid = [
      file('   '), file('folder/name.md'), file('bad\\name.md'), file('bad\u0000name.md'),
      file(`${'😀'.repeat(253)}.md`), file('notes.exe'), file('empty.md', ''),
      file('large.pdf', new Uint8Array(5 * 1024 * 1024 + 1)),
    ];
    for (const selected of invalid) {
      const message = validateUploadFile(selected);
      expect(message).not.toBeNull();
      expect(await rejection(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, selected,
        new AbortController().signal))).toMatchObject({ message, status: null });
    }
    for (const name of ['text.TXT', 'text.Md', 'text.PdF']) expect(validateUploadFile(file(name))).toBeNull();
    expect(validateUploadFile(file('limit.pdf', new Uint8Array(5 * 1024 * 1024)))).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects invalid parent scope and a pre-cancelled operation without reading or sending files', async () => {
    const selected = file();
    const read = vi.spyOn(selected, 'arrayBuffer');
    for (const [workspace, knowledgeBase] of [
      [PRIVATE, KNOWLEDGE_BASE_ID], [WORKSPACE_ID, `${KNOWLEDGE_BASE_ID}\n`],
    ]) {
      await rejection(uploadDocument(workspace, knowledgeBase, selected, new AbortController().signal));
    }
    const controller = new AbortController();
    controller.abort();
    expect(await rejection(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, selected, controller.signal)))
      .toMatchObject({ message: '请求已取消。' });
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('does not send a late upload when cancelled during file reading or hashing', async () => {
    const selected = file();
    let finishRead!: (bytes: ArrayBuffer) => void;
    vi.spyOn(selected, 'arrayBuffer').mockImplementationOnce(() => new Promise((resolve) => { finishRead = resolve; }));
    const controller = new AbortController();
    const pending = rejection(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, selected, controller.signal));
    controller.abort();
    finishRead(new TextEncoder().encode(CONTENT).buffer);
    expect(await pending).toMatchObject({ message: '请求已取消。' });

    const second = new AbortController();
    vi.spyOn(selected, 'arrayBuffer').mockResolvedValueOnce(new TextEncoder().encode(CONTENT).buffer);
    let finishHash!: (hash: ArrayBuffer) => void;
    const hashing = new Promise<void>((resolve) => {
      vi.spyOn(webcrypto.subtle, 'digest').mockImplementationOnce(() => {
        resolve();
        return new Promise<ArrayBuffer>((done) => { finishHash = done; });
      });
    });
    const late = rejection(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, selected, second.signal));
    await hashing;
    second.abort();
    finishHash(new Uint8Array(32).buffer);
    expect(await late).toMatchObject({ message: '请求已取消。' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('keeps local read failures separate from an unconfirmed server result', async () => {
    const selected = file();
    vi.spyOn(selected, 'arrayBuffer').mockRejectedValueOnce(new Error(PRIVATE));
    const error = await rejection(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, selected,
      new AbortController().signal));
    expect(error).not.toBeInstanceOf(UploadUnconfirmedError);
    expect(error.message).toBe('无法读取所选文件，请重新选择后重试。');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ['bad id', { ...PARSED, id: PRIVATE }],
    ['different parent', { ...PARSED, knowledge_base_id: WORKSPACE_ID }],
    ['different filename', { ...PARSED, original_filename: 'other.md' }],
    ['different byte size', { ...PARSED, byte_size: BYTE_SIZE + 1 }],
    ['string byte size', { ...PARSED, byte_size: String(BYTE_SIZE) }],
    ['different digest', { ...PARSED, sha256: '0'.repeat(64) }],
    ['ready status', { ...PARSED, status: 'ready' }],
    ['parsed with error', { ...PARSED, error_code: 'EMPTY_TEXT' }],
    ['failed without error', { ...PARSED, status: 'failed' }],
    ['null body', null],
  ])('does not confirm an invalid 201 response: %s', async (_label, payload) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(payload));
    await expectUnconfirmed(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, file(), new AbortController().signal));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([200, 202, 204, 400, 409, 500, 503])('treats unexpected HTTP %s as unconfirmed without parsing details', async (status) => {
    const response = status === 204 ? new Response(null, { status }) : jsonResponse({ detail: PRIVATE }, status);
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    await expectUnconfirmed(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, file(), new AbortController().signal));
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([401, 403, 404, 413, 415, 422])('preserves definite HTTP %s refusal without server details or retry', async (status) => {
    const response = jsonResponse({ detail: PRIVATE }, status);
    const read = vi.spyOn(response, 'json');
    fetchMock.mockResolvedValueOnce(response);
    const error = await rejection(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, file(), new AbortController().signal));
    expect(error).not.toBeInstanceOf(UploadUnconfirmedError);
    expect(error.status).toBe(status);
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('contains invalid JSON and network failure without claiming the upload was rejected or retrying', async () => {
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE, { status: 201 }));
    await expectUnconfirmed(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, file(), new AbortController().signal));
    fetchMock.mockRejectedValueOnce(new Error(PRIVATE));
    await expectUnconfirmed(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, file(), new AbortController().signal));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('aborts an in-flight upload as unconfirmed and never resends', async () => {
    const started = waitForFetch();
    const controller = new AbortController();
    const pending = expectUnconfirmed(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, file(), controller.signal));
    await started;
    controller.abort();
    await pending;
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('times out after ten seconds as unconfirmed and cleans its timer without retry', async () => {
    // Read bytes before fake timers so the FileReader is not tied to the request clock.
    const selected = file();
    const bytes = await readFile(selected);
    vi.spyOn(selected, 'arrayBuffer').mockResolvedValueOnce(bytes);
    const started = waitForFetch();
    vi.useFakeTimers();
    const pending = expectUnconfirmed(uploadDocument(WORKSPACE_ID, KNOWLEDGE_BASE_ID, selected,
      new AbortController().signal));
    await started;
    await vi.advanceTimersByTimeAsync(10_000);
    await pending;
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
