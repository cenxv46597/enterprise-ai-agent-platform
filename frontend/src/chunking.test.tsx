import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';

const USER = '00000000-0000-0000-0000-000000000001';
const SPACE = '00000000-0000-0000-0000-00000000000a';
const KB = '00000000-0000-0000-0000-000000000101';
const DOCUMENT_A = '00000000-0000-0000-0000-000000000301';
const DOCUMENT_B = '00000000-0000-0000-0000-000000000302';
const RESTORE = '/api/v1/auth/web/session';
const LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces';
const KNOWLEDGE = `${SPACES}/${SPACE}/knowledge-bases`;
const DOCUMENTS = `${KNOWLEDGE}/${KB}/documents`;
const DETAIL_A = `${DOCUMENTS}/${DOCUMENT_A}`;
const DETAIL_B = `${DOCUMENTS}/${DOCUMENT_B}`;
const CHUNKS_A = `${DETAIL_A}/chunks`;
const CHUNKS_B = `${DETAIL_B}/chunks`;
const FILENAME = '同名资料.md';
const PRIVATE = 'PRIVATE_CHUNK_RESPONSE_MUST_NOT_BE_DISPLAYED';
const UNCONFIRMED = '切块结果未确认；未自动重试。请刷新文档与切块记录后再决定是否重新执行。';

type Status = 'uploaded' | 'processing' | 'parsed' | 'ready' | 'failed';
type Route = (request: Request) => Response | Promise<Response> | undefined;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function sessionResponse(expiresAt = Date.now() / 1000 + 1800) {
  return json({ user: { id: USER, username: 'demo_a' }, expires_at: expiresAt });
}

function documentRow(id = DOCUMENT_A, status: Status = 'parsed', name = FILENAME) {
  return { id, knowledge_base_id: KB, original_filename: name, status, error_code: PRIVATE };
}

function chunkRows(contents = ['第一段正文'], documentId = DOCUMENT_A) {
  let nextStart = 0;
  return contents.map((content, index) => {
    const start = nextStart;
    nextStart += Array.from(content).length;
    return {
      id: `00000000-0000-0000-0000-${(0x500 + index).toString(16).padStart(12, '0')}`,
      document_id: documentId, chunk_index: index + 1, content,
      start_char: start, end_char: nextStart,
    };
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function pathOf(request: Request) { return new URL(request.url).pathname; }
function posts(requests: Request[]) {
  return requests.filter(request => request.method === 'POST' && pathOf(request).endsWith('/chunks'));
}

// 不替模拟 fetch 阻止迟到响应，让应用自己的请求隔离接受验证。
function installApi(route: Route = () => undefined) {
  const requests: Request[] = [];
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request
      ? new Request(input, init)
      : new Request(new URL(String(input), 'http://localhost'), init);
    requests.push(request);
    const response = route(request);
    if (response !== undefined) return response;
    switch (pathOf(request)) {
      case RESTORE:
      case '/api/v1/auth/web/login': return sessionResponse();
      case LOGOUT: return new Response(null, { status: 204 });
      case SPACES: return json([{ id: SPACE, name: '甲空间' }]);
      case KNOWLEDGE: return json([{ id: KB, workspace_id: SPACE, name: '甲知识库' }]);
      case DOCUMENTS: return json([documentRow()]);
      case DETAIL_A: return json(documentRow());
      case DETAIL_B: return json(documentRow(DOCUMENT_B));
      case CHUNKS_A: return json(request.method === 'POST' ? chunkRows() : []);
      case CHUNKS_B: return json([]);
      default: throw new Error('Unexpected mocked endpoint');
    }
  }));
  return requests;
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

async function openDocuments() {
  await advance();
  await click(screen.getByRole('button', { name: '进入甲空间' }));
  await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
}

async function openProcessing() {
  await openDocuments();
  await click(screen.getByRole('button', { name: `查看${FILENAME}的处理详情` }));
}

function expectNoPrivateOutput() {
  expect(document.body.textContent).not.toContain(PRIVATE);
  for (const id of [USER, SPACE, KB, DOCUMENT_A, DOCUMENT_B]) {
    expect(document.body.textContent).not.toContain(id);
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('独立文档处理页与显式切块', () => {
  it.each(['parsed', 'ready'] as const)('同名条目按 ID 进入，先读取最新详情再读已有块，%s 不自动触发切块', async status => {
    const detail = deferred<Response>();
    const chunks = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === DOCUMENTS) return json([documentRow(), documentRow(DOCUMENT_B)]);
      if (pathOf(request) === DETAIL_B) return detail.promise;
      if (pathOf(request) === CHUNKS_B) return chunks.promise;
    });
    render(<App />);
    await openDocuments();
    expect(requests.filter(request => pathOf(request).startsWith(`${DOCUMENTS}/`))).toHaveLength(0);
    const cards = screen.getAllByRole('heading', { level: 2, name: FILENAME }).map(heading => heading.closest('li')!);
    const entry = within(cards[1]).getByRole('button', { name: `查看${FILENAME}的处理详情` });
    expect(entry.textContent).toBe('查看与切块');
    await click(entry);
    expect(screen.getByRole('heading', { level: 1, name: '文档处理' })).toBeTruthy();
    expect(screen.queryByLabelText('选择文档')).toBeNull();
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(true);
    expect(requests.filter(request => pathOf(request) === DETAIL_B)).toHaveLength(1);
    expect(requests.filter(request => pathOf(request) === CHUNKS_B)).toHaveLength(0);
    await act(async () => {
      detail.resolve(json(documentRow(DOCUMENT_B, status)));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(requests.filter(request => pathOf(request) === CHUNKS_B)).toHaveLength(1);
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { chunks.resolve(json([])); await vi.advanceTimersByTimeAsync(0); });
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(false);
    expect(posts(requests)).toHaveLength(0);
    expect(requests.filter(request => pathOf(request) === DETAIL_A)).toHaveLength(0);
    expect(requests.every(request => !/embed|agent|answer/i.test(pathOf(request)))).toBe(true);
    expectNoPrivateOutput();
  });

  it('最新详情已变为 failed 时保留只读状态并禁止切块，不能沿用列表里的 parsed', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === DETAIL_A) return json(documentRow(DOCUMENT_A, 'failed'));
    });
    render(<App />);
    await openProcessing();
    expect(screen.getByText('解析失败')).toBeTruthy();
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(true);
    await click(screen.getByRole('button', { name: '切块' }));
    expect(posts(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it('已有正文以文字转义，预览只展示前三块及每块前三百码点', async () => {
    const unsafe = '<img src=x onerror=alert(1)>';
    const first = unsafe + '🙂'.repeat(310) + 'FIRST_BLOCK_HIDDEN_TAIL';
    const second = '第二块正文';
    const third = '第三块正文';
    const fourth = 'FOURTH_BLOCK_MUST_NOT_BE_PREVIEWED';
    const requests = installApi(request => {
      if (pathOf(request) === CHUNKS_A) return json(chunkRows([first, second, third, fourth]));
    });
    const { container } = render(<App />);
    await openProcessing();
    expect(container.querySelector('img')).toBeNull();
    expect(document.body.textContent).toContain(Array.from(first).slice(0, 300).join(''));
    expect(document.body.textContent).not.toContain('FIRST_BLOCK_HIDDEN_TAIL');
    expect(screen.getByText(second)).toBeTruthy();
    expect(screen.getByText(third)).toBeTruthy();
    expect(screen.queryByText(fourth)).toBeNull();
    expect(posts(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it('显式发送固定参数一次，切块成功后的详情刷新失败仍保留成功结果和返回正文', async () => {
    let detailReads = 0;
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === DETAIL_A && ++detailReads > 1) return Promise.reject(new TypeError(PRIVATE));
      if (pathOf(request) === CHUNKS_A && request.method === 'POST') return pending.promise;
    });
    render(<App />);
    await openProcessing();
    await click(screen.getByRole('button', { name: '切块' }));
    const runningButton = screen.getByRole('button', {
      name: '正在切块…',
    }) as HTMLButtonElement;
    expect(runningButton.disabled).toBe(true);
    await click(runningButton);
    expect(posts(requests)).toHaveLength(1);
    const [post] = posts(requests);
    expect(await post.clone().json()).toEqual({ chunk_size: 500, overlap: 50 });
    expect(post.credentials).toBe('same-origin');
    expect(post.headers.get('X-EAAP-Web')).toBe('1');
    expect(post.headers.get('Content-Type')).toBe('application/json');
    expect(post.headers.has('Authorization')).toBe(false);
    await act(async () => { pending.resolve(json(chunkRows(['成功返回正文']))); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('button', { name: '切块' })).toBeTruthy();
    expect(screen.getByText('切块请求成功，返回 1 个文本块。')).toBeTruthy();
    expect(screen.getByText('成功返回正文')).toBeTruthy();
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(detailReads).toBe(2);
    expect(posts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('网络故障保留未确认提示、不自动重发，成功刷新详情和已有块后才允许再次执行', async () => {
    let chunkReads = 0;
    const requests = installApi(request => {
      if (pathOf(request) !== CHUNKS_A) return;
      if (request.method === 'POST') return Promise.reject(new TypeError(PRIVATE));
      if (++chunkReads > 1) return json(chunkRows(['刷新核对后的正文']));
    });
    render(<App />);
    await openProcessing();
    await click(screen.getByRole('button', { name: '切块' }));
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(true);
    await advance(30_000);
    expect(posts(requests)).toHaveLength(1);
    await click(screen.getByRole('button', { name: '刷新文档与切块' }));
    expect(screen.getByText('刷新核对后的正文')).toBeTruthy();
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(false);
    expect(posts(requests)).toHaveLength(1);
    expect(chunkReads).toBe(2);
    expectNoPrivateOutput();
  });

  it('409 拒绝不回显后端 detail，不当作成功或未确认并要求核对后端状态', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === CHUNKS_A && request.method === 'POST') return json({ detail: PRIVATE }, 409);
    });
    render(<App />);
    await openProcessing();
    await click(screen.getByRole('button', { name: '切块' }));
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    expect(screen.queryByText(/切块请求成功/)).toBeNull();
    expect(posts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('切块返回401清除身份、所选文档与正文，重新登录从空间列表开始', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === CHUNKS_A) {
        return request.method === 'POST' ? json({ detail: PRIVATE }, 401) : json(chunkRows(['登录失效前的正文']));
      }
    });
    render(<App />);
    await openProcessing();
    expect(screen.getByText('登录失效前的正文')).toBeTruthy();
    await click(screen.getByRole('button', { name: '切块' }));
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.queryByText('登录失效前的正文')).toBeNull();
    expect(screen.queryByText(FILENAME)).toBeNull();
    expect(screen.queryByText('甲知识库')).toBeNull();
    await act(async () => {
      fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'demo_a' } });
      fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'fake-chunk-test-password' } });
    });
    await click(screen.getByRole('button', { name: '登录' }));
    expect(screen.getByRole('heading', { level: 1, name: '你的工作空间' })).toBeTruthy();
    expect(screen.queryByText(FILENAME)).toBeNull();
    expect(posts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('停止等待取消浏览器请求并显示未确认，迟到成功不能改回成功或重新开放切块', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === CHUNKS_A && request.method === 'POST') return pending.promise;
    });
    render(<App />);
    await openProcessing();
    await click(screen.getByRole('button', { name: '切块' }));
    await click(screen.getByRole('button', { name: '停止等待切块结果' }));
    expect(posts(requests)[0].signal.aborted).toBe(true);
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    await act(async () => { pending.resolve(json(chunkRows(['取消后迟到正文']))); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.queryByText('取消后迟到正文')).toBeNull();
    expect(screen.queryByText(/切块请求成功/)).toBeNull();
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(true);
    expect(posts(requests)).toHaveLength(1);
  });

  it('详情404保留上下文错误，既不读块也不冒充零块或可切块', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === DETAIL_A) return json({ detail: PRIVATE }, 404);
    });
    render(<App />);
    await openProcessing();
    expect(screen.getByRole('heading', { level: 1, name: '文档处理' })).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('不存在或你已无权访问');
    expect(document.body.textContent).toContain('甲知识库');
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(true);
    expect(requests.filter(request => pathOf(request) === CHUNKS_A)).toHaveLength(0);
    expect(posts(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it.each([200, 404])('返回重新读取文档列表，改进另一文档后隔离旧详情（%i）', async lateStatus => {
    const pending = deferred<Response>();
    let lists = 0;
    const requests = installApi(request => {
      if (pathOf(request) === DOCUMENTS && ++lists > 1) return json([documentRow(DOCUMENT_B, 'parsed', '另一份.md')]);
      if (pathOf(request) === DETAIL_A) return pending.promise;
      if (pathOf(request) === DETAIL_B) return json(documentRow(DOCUMENT_B, 'parsed', '另一份.md'));
    });
    render(<App />);
    await openProcessing();
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(lists).toBe(2);
    expect(requests.find(request => pathOf(request) === DETAIL_A)!.signal.aborted).toBe(true);
    await click(screen.getByRole('button', { name: '查看另一份.md的处理详情' }));
    await act(async () => {
      pending.resolve(lateStatus === 200 ? json(documentRow(DOCUMENT_A, 'parsed', '旧详情迟到.md')) : json({ detail: PRIVATE }, 404));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(document.body.textContent).toContain('另一份.md');
    expect(screen.queryByText('旧详情迟到.md')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(requests.filter(request => pathOf(request) === CHUNKS_A)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it.each([200, 404])('切块尚未结束就返回列表，隔离迟到写入结果（%i）', async lateStatus => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === CHUNKS_A && request.method === 'POST') return pending.promise;
    });
    render(<App />);
    await openProcessing();
    await click(screen.getByRole('button', { name: '切块' }));
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(posts(requests)[0].signal.aborted).toBe(true);
    await act(async () => {
      pending.resolve(lateStatus === 200 ? json(chunkRows(['迟到写入正文'])) : json({ detail: PRIVATE }, 404));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    expect(screen.queryByText('迟到写入正文')).toBeNull();
    expect(screen.queryByText(/切块请求成功/)).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(posts(requests)).toHaveLength(1);
    expect(requests.filter(request => pathOf(request) === DOCUMENTS)).toHaveLength(2);
    expectNoPrivateOutput();
  });

  it('退出中止切块，退出失败回来仍是未确认，迟到切块不能覆盖退出错误', async () => {
    const pending = deferred<Response>();
    const exiting = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === CHUNKS_A && request.method === 'POST') return pending.promise;
      if (pathOf(request) === LOGOUT) return exiting.promise;
    });
    render(<App />);
    await openProcessing();
    await click(screen.getByRole('button', { name: '切块' }));
    await click(screen.getByRole('button', { name: '退出登录' }));
    expect(posts(requests)[0].signal.aborted).toBe(true);
    await act(async () => {
      pending.resolve(json(chunkRows(['退出中迟到正文'])));
      exiting.reject(new TypeError(PRIVATE));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { level: 1, name: '文档处理' })).toBeTruthy();
    expect(screen.getByText(UNCONFIRMED)).toBeTruthy();
    expect(document.body.textContent).toContain('退出尚未确认');
    expect(screen.queryByText('退出中迟到正文')).toBeNull();
    expect(screen.queryByText(/切块请求成功/)).toBeNull();
    expect((screen.getByRole('button', { name: '切块' }) as HTMLButtonElement).disabled).toBe(true);
    expectNoPrivateOutput();
  });

  it('固定期限到期清除处理页并中断切块，迟到404不能修改新登录页', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === RESTORE) return sessionResponse(Date.now() / 1000 + 60);
      if (pathOf(request) === CHUNKS_A && request.method === 'POST') return pending.promise;
    });
    render(<App />);
    await openProcessing();
    await click(screen.getByRole('button', { name: '切块' }));
    await advance(60_000);
    expect(posts(requests)[0].signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toBe('登录已到期，请重新登录。');
    await act(async () => { pending.resolve(json({ detail: PRIVATE }, 404)); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('alert').textContent).toBe('登录已到期，请重新登录。');
    expect(screen.queryByText(FILENAME)).toBeNull();
    expect(screen.queryByText(UNCONFIRMED)).toBeNull();
    expect(screen.queryByText('甲知识库')).toBeNull();
    expect(requests.filter(request => pathOf(request) === RESTORE)).toHaveLength(1);
    expectNoPrivateOutput();
  });
});
