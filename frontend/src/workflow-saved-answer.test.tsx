import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { USER_ID, WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, OTHER_DOCUMENT_ID,
  DOCUMENT_PATH, OTHER_DOCUMENT_PATH, QUESTION, ANSWER, LONG_QUOTE, OTHER_QUOTE, PRIVATE,
  documentResponse, jsonResponse } from './conversation-test-fixtures';
import { WORKFLOWS_PATH, WORKFLOW_PATH, WORKFLOW_EXECUTE_PATH, CREATION_REQUEST_ID, WORKFLOW_EXECUTION_REQUEST_ID,
  createdWorkflow, completedWorkflow, failedWorkflow, workflowExecution } from './workflow-test-fixtures';

const RESTORE = '/api/v1/auth/web/session', LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces', KNOWLEDGE = `${SPACES}/${WORKSPACE_ID}/knowledge-bases`;
const DOCUMENTS = `${KNOWLEDGE}/${KNOWLEDGE_BASE_ID}/documents`;
const CHUNKS_PATH = `${DOCUMENT_PATH}/chunks`, OTHER_CHUNKS_PATH = `${OTHER_DOCUMENT_PATH}/chunks`;
type Route = (request: Request) => Response | Promise<Response> | undefined;
function pathname(request: Request) { return new URL(request.url).pathname; }
function deferred() {
  let resolve!: (response: Response) => void;
  return { promise: new Promise<Response>(done => { resolve = done; }), resolve: (response: Response) => resolve(response) };
}

// Each document has one complete first chunk. The second checkpoint source is deliberately not quoted.
function savedWorkflow() {
  const value = completedWorkflow();
  value.checkpoint.hits = value.checkpoint.hits.map(hit => ({ ...hit, chunk_index: 1,
    start_char: 0, end_char: Array.from(hit.content).length }));
  const first = value.checkpoint.hits[0];
  value.result.citations = [{ chunk_id: first.chunk_id, document_id: first.document_id,
    document_name: first.document_name, chunk_index: first.chunk_index,
    start_char: first.start_char, end_char: first.end_char, quote: first.content }];
  return value;
}
function savedChunks(documentId = DOCUMENT_ID) {
  return savedWorkflow().checkpoint.hits.filter(hit => hit.document_id === documentId).map(hit => ({
    id: hit.chunk_id, document_id: hit.document_id, chunk_index: hit.chunk_index,
    content: hit.content, start_char: hit.start_char, end_char: hit.end_char,
  }));
}
function install(route: Route = () => undefined) {
  const requests: Request[] = [];
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const request = new Request(new URL(String(input), 'http://localhost'), init); requests.push(request);
    const response = route(request); if (response !== undefined) return response;
    switch (pathname(request)) {
      case RESTORE: return jsonResponse({ user: { id: USER_ID, username: 'demo_a' }, expires_at: Date.now() / 1000 + 1800 });
      case LOGOUT: return new Response(null, { status: 204 });
      case SPACES: return jsonResponse([{ id: WORKSPACE_ID, name: '甲空间' }]);
      case KNOWLEDGE: return jsonResponse([{ id: KNOWLEDGE_BASE_ID, workspace_id: WORKSPACE_ID, name: '甲知识库' }]);
      case DOCUMENTS: return jsonResponse([{ ...documentResponse(), status: 'parsed' }]);
      case DOCUMENT_PATH: return jsonResponse(documentResponse());
      case OTHER_DOCUMENT_PATH: return jsonResponse(documentResponse(OTHER_DOCUMENT_ID));
      case CHUNKS_PATH: return jsonResponse(savedChunks());
      case OTHER_CHUNKS_PATH: return jsonResponse(savedChunks(OTHER_DOCUMENT_ID));
      case WORKFLOWS_PATH: return jsonResponse(createdWorkflow(), 201);
      case WORKFLOW_EXECUTE_PATH: return jsonResponse({ detail: PRIVATE }, 503);
      case WORKFLOW_PATH: return jsonResponse(savedWorkflow());
      default: throw new Error('Unexpected saved-answer mock path');
    }
  }));
  return requests;
}
async function advance(ms = 0) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function click(element: HTMLElement) { await act(async () => { fireEvent.click(element); await vi.advanceTimersByTimeAsync(0); }); }
async function resolve(pending: ReturnType<typeof deferred>, response: Response) {
  await act(async () => { pending.resolve(response); await vi.advanceTimersByTimeAsync(0); });
}
async function open() {
  render(<App />); await advance(); await click(screen.getByRole('button', { name: '进入甲空间' }));
  await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
  await click(screen.getByRole('button', { name: '问答工作流' }));
}
async function create() {
  await act(async () => { fireEvent.change(screen.getByLabelText('工作流问题'), { target: { value: QUESTION } }); });
  await click(screen.getByRole('button', { name: '创建工作流' }));
}
async function prepare() {
  await open(); await create(); await click(screen.getByRole('button', { name: '执行工作流一次' }));
  await readStatus();
}
async function readStatus() { await click(screen.getByRole('button', { name: '读取当前工作流状态' })); }
async function readAnswer() { await click(screen.getByRole('button', { name: '查看已保存答案' })); }
function posts(requests: Request[]) { return requests.filter(item => item.method === 'POST' && pathname(item) === WORKFLOW_EXECUTE_PATH); }
function sources(requests: Request[]) { return requests.filter(item => [DOCUMENT_PATH, OTHER_DOCUMENT_PATH, CHUNKS_PATH, OTHER_CHUNKS_PATH].includes(pathname(item))); }
function savedRegion() { return screen.getByRole('region', { name: '已保存答案（只读）' }); }
function expectNoAnswer() {
  for (const value of [ANSWER, LONG_QUOTE, OTHER_QUOTE, PRIVATE]) expect(document.body.textContent).not.toContain(value);
}
function expectReadDisabled() { expect((screen.getByRole('button', { name: '查看已保存答案' }) as HTMLButtonElement).disabled).toBe(true); }

const TRACE_A = '11111111-1111-4111-8111-111111111111';
const TRACE_B = '22222222-2222-4222-8222-222222222222';
function traced(payload: unknown, status = 200, trace: string | null = TRACE_A) {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (trace !== null) headers.set('X-Trace-Id', trace);
  return new Response(JSON.stringify(payload), { status, headers });
}
function failed(trace: string | null = TRACE_B, status = 503) {
  return traced({ detail: PRIVATE, trace_id: TRACE_A }, status, trace);
}
function stateRegion() { return screen.getByRole('region', { name: '当前工作流状态' }); }
function expectTrace(region: HTMLElement, trace = TRACE_B) {
  expect(within(region).getByText(trace, { selector: 'code' })).toBeTruthy();
  expect(region.textContent).toContain('反馈问题时请提供此编号');
  expect(document.body.textContent).not.toContain(PRIVATE);
}
function expectNoTrace() { expect(document.body.textContent).not.toContain('排错编号：'); }
function expectOnlyReads(requests: Request[], start: number) {
  expect(requests.slice(start).every(request => request.method === 'GET')).toBe(true);
  expect(requests.every(request => !request.headers.has('X-Trace-Id'))).toBe(true);
}

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
  vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce(CREATION_REQUEST_ID).mockReturnValue(WORKFLOW_EXECUTION_REQUEST_ID) });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('手动查看已保存工作流答案', () => {
  it('新建后和首次执行未知后均不能跳过completed状态读取；状态GET只投影元数据', async () => {
    const requests = install(); await open(); await create(); expectReadDisabled();
    await readAnswer(); expect(requests.filter(item => pathname(item) === WORKFLOW_PATH)).toHaveLength(0);
    await click(screen.getByRole('button', { name: '执行工作流一次' })); expectReadDisabled();
    const count = requests.length; await readStatus();
    expect(requests.slice(count).map(pathname)).toEqual([WORKFLOW_PATH]);
    expect(sources(requests)).toHaveLength(0); expectNoAnswer();
    expect((screen.getByRole('button', { name: '查看已保存答案' }) as HTMLButtonElement).disabled).toBe(false);
    expect(posts(requests)).toHaveLength(1);
  });

  it('首次执行503后，手动完成状态与完整来源核对可展示已保存正文，但不制造执行回执或重复POST', async () => {
    const requests = install(); await prepare(); const count = requests.length; await readAnswer();
    const region = savedRegion();
    expect(region.textContent).toContain(QUESTION); expect(region.textContent).toContain(ANSWER);
    expect(region.textContent).toContain(LONG_QUOTE); expect(region.textContent).not.toContain(OTHER_QUOTE);
    expect(document.querySelector('img')).toBeNull();
    expect(requests.slice(count).map(pathname)).toEqual([WORKFLOW_PATH, DOCUMENT_PATH, CHUNKS_PATH, OTHER_DOCUMENT_PATH, OTHER_CHUNKS_PATH]);
    expect(screen.queryByRole('heading', { name: '本次执行回执' })).toBeNull();
    expect(document.body.textContent).toContain('工作流执行结果未确认');
    expect(region.textContent).toContain('不能据此确认先前超时请求的执行回执');
    expect((screen.getByRole('button', { name: '执行工作流一次' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('button', { name: '确认并恢复生成一次' })).toBeNull();
    await advance(60_000); expect(posts(requests)).toHaveLength(1);
    expect(requests.some(item => /\/(search|answers|messages)$/.test(pathname(item)))).toBe(false);
  });

  it('首次执行等待被取消后仍能独立查看保存结果，迟到原回执不能改变已保存答案身份', async () => {
    const pending = deferred();
    const requests = install(request => pathname(request) === WORKFLOW_EXECUTE_PATH ? pending.promise : undefined);
    await open(); await create(); await click(screen.getByRole('button', { name: '执行工作流一次' }));
    await click(screen.getByRole('button', { name: '停止等待执行结果' })); await readStatus(); await readAnswer();
    await resolve(pending, jsonResponse(workflowExecution(savedWorkflow())));
    expect(savedRegion().textContent).toContain(ANSWER);
    expect(document.body.textContent).toContain('工作流执行结果未确认');
    expect(screen.queryByRole('heading', { name: '本次执行回执' })).toBeNull(); expect(posts(requests)).toHaveLength(1);
  });

  it.each(['content', 'id', 'index', 'range'] as const)('未被引用的末来源块%s不一致也整体隐藏正文', async field => {
    const chunks = savedChunks(OTHER_DOCUMENT_ID);
    if (field === 'content') chunks[0].content = '变'.repeat(Array.from(chunks[0].content).length);
    if (field === 'id') chunks[0].id = '11111111-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
    if (field === 'index') chunks[0].chunk_index = 2;
    if (field === 'range') chunks[0].end_char += 1;
    const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH ? jsonResponse(chunks) : undefined);
    await prepare(); await readAnswer(); expectNoAnswer(); expectReadDisabled();
    expect(savedRegion().textContent).toContain('已保存答案尚未核对通过');
    expect(sources(requests).map(pathname)).toEqual([DOCUMENT_PATH, CHUNKS_PATH, OTHER_DOCUMENT_PATH, OTHER_CHUNKS_PATH]);
    expect(posts(requests)).toHaveLength(1);
  });

  it('全部来源通过前不显示正文；等待按钮禁用且双击不会重复读取或提交', async () => {
    const pending = deferred(); const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH ? pending.promise : undefined);
    await prepare(); await readAnswer(); expectNoAnswer();
    const button = screen.getByRole('button', { name: '正在核对已保存答案…' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true); const count = requests.length; await click(button); await click(button);
    expect(requests).toHaveLength(count); expect(posts(requests)).toHaveLength(1);
    await resolve(pending, jsonResponse(savedChunks(OTHER_DOCUMENT_ID)));
    expect(savedRegion().textContent).toContain(ANSWER); expect(posts(requests)).toHaveLength(1);
  });

  it.each(['cancel', 'back', 'logout', 'expiry'] as const)('保存答案GET等待时%s使迟到结果失效', async action => {
    const pending = deferred(); let reads = 0;
    const requests = install(request => pathname(request) === WORKFLOW_PATH && ++reads === 2 ? pending.promise : undefined);
    await prepare(); await readAnswer(); expectNoAnswer();
    if (action === 'cancel') await click(screen.getByRole('button', { name: '停止查看答案' }));
    else if (action === 'back') await click(screen.getByRole('button', { name: '返回文档列表' }));
    else if (action === 'logout') await click(screen.getByRole('button', { name: '退出登录' }));
    else await advance(1_800_000);
    await resolve(pending, jsonResponse(savedWorkflow())); expectNoAnswer();
    expect(posts(requests)).toHaveLength(1); expect(sources(requests)).toHaveLength(0);
    if (action === 'cancel') expectReadDisabled();
    if (action === 'logout' || action === 'expiry') expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();
  });

  it.each(['cancel', 'back', 'logout', 'expiry'] as const)('末来源文本块等待时%s隔离迟到正文', async action => {
    const pending = deferred(); const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH ? pending.promise : undefined);
    await prepare(); await readAnswer(); expectNoAnswer();
    if (action === 'cancel') await click(screen.getByRole('button', { name: '停止查看答案' }));
    else if (action === 'back') await click(screen.getByRole('button', { name: '返回文档列表' }));
    else if (action === 'logout') await click(screen.getByRole('button', { name: '退出登录' }));
    else await advance(1_800_000);
    await resolve(pending, jsonResponse(savedChunks(OTHER_DOCUMENT_ID))); expectNoAnswer(); expect(posts(requests)).toHaveLength(1);
    if (action === 'cancel') expectReadDisabled();
  });

  it('查看中退出失败仍隔离迟到来源，并要求重新读取状态', async () => {
    const pending = deferred();
    const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH ? pending.promise
      : pathname(request) === LOGOUT ? jsonResponse({ detail: PRIVATE }, 502) : undefined);
    await prepare(); await readAnswer(); await click(screen.getByRole('button', { name: '退出登录' }));
    await resolve(pending, jsonResponse(savedChunks(OTHER_DOCUMENT_ID))); expectNoAnswer(); expectReadDisabled();
    expect(posts(requests)).toHaveLength(1);
  });

  it.each(['workflow', 'source'] as const)('查看%s返回404清除旧的受保护答案与已完成状态门控', async target => {
    let reads = 0, sourceReads = 0;
    const requests = install(request => {
      if (pathname(request) === WORKFLOW_PATH && ++reads === 3 && target === 'workflow') return jsonResponse({ detail: PRIVATE }, 404);
      if (pathname(request) === OTHER_CHUNKS_PATH && ++sourceReads === 2 && target === 'source') return jsonResponse({ detail: PRIVATE }, 404);
      return undefined;
    });
    await prepare(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER);
    await readAnswer(); expectNoAnswer(); expectReadDisabled(); expect(posts(requests)).toHaveLength(1);
  });

  it.each(['workflow', 'source'] as const)('查看%s返回401清除答案并退出登录', async target => {
    let reads = 0;
    const requests = install(request => pathname(request) === WORKFLOW_PATH && ++reads === 2 && target === 'workflow'
      || pathname(request) === OTHER_CHUNKS_PATH && target === 'source' ? jsonResponse({ detail: PRIVATE }, 401) : undefined);
    await prepare(); await readAnswer(); expectNoAnswer();
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy(); expect(posts(requests)).toHaveLength(1);
  });

  it('再次读取状态立即移除保存正文；读取失败不保留旧completed门控', async () => {
    const pending = deferred(); let reads = 0;
    const requests = install(request => pathname(request) === WORKFLOW_PATH && ++reads === 3 ? pending.promise : undefined);
    await prepare(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER);
    await readStatus(); expectNoAnswer(); expectReadDisabled();
    await resolve(pending, jsonResponse({ detail: PRIVATE }, 503)); expectNoAnswer(); expectReadDisabled();
    expect(document.body.textContent).toContain('工作流当前状态未确认'); expect(posts(requests)).toHaveLength(1);
  });

  it('重新读取状态成功也只保留元数据，需要再次明确查看', async () => {
    const requests = install(); await prepare(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER);
    const count = requests.length; await readStatus(); expectNoAnswer();
    expect(requests.slice(count).map(pathname)).toEqual([WORKFLOW_PATH]);
    expect((screen.getByRole('button', { name: '查看已保存答案' }) as HTMLButtonElement).disabled).toBe(false);
    expect(posts(requests)).toHaveLength(1);
  });

  it('状态GET404清除先前已保存正文，失败状态也不能直接读取答案', async () => {
    let reads = 0; const requests = install(request => pathname(request) === WORKFLOW_PATH && ++reads >= 3
      ? jsonResponse({ detail: PRIVATE }, 404) : undefined);
    await prepare(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER);
    await readStatus(); expectNoAnswer(); expectReadDisabled(); expect(posts(requests)).toHaveLength(1);
  });

  it('GET已确认failed而非completed不会开放只读答案入口', async () => {
    const requests = install(request => pathname(request) === WORKFLOW_PATH ? jsonResponse(failedWorkflow('generate')) : undefined);
    await prepare(); expectReadDisabled(); await readAnswer(); expectNoAnswer();
    expect(sources(requests)).toHaveLength(0); expect(posts(requests)).toHaveLength(1);
  });

  it('成功执行的历史正文也在另一次只读核对开始时清除，不能被末来源失败掩盖', async () => {
    const pending = deferred();
    const requests = install(request => pathname(request) === WORKFLOW_EXECUTE_PATH ? jsonResponse(workflowExecution(savedWorkflow()))
      : pathname(request) === OTHER_CHUNKS_PATH ? pending.promise : undefined);
    await prepare(); expect(document.body.textContent).toContain(ANSWER); await readAnswer(); expectNoAnswer();
    await resolve(pending, jsonResponse([], 200)); expectNoAnswer(); expectReadDisabled();
    expect(within(savedRegion()).queryByText(ANSWER)).toBeNull(); expect(posts(requests)).toHaveLength(1);
  });
});

describe('创建页只读工作流排错编号', () => {
  it.each([403, 404, 409, 422, 503, 201])('状态GET失败%s只展示自身编号且不补发写请求', async status => {
    const requests = install(request => pathname(request) === WORKFLOW_PATH ? failed(TRACE_B, status) : undefined);
    await open(); await create(); const start = requests.length; await readStatus();
    expectTrace(stateRegion()); expect(savedRegion().textContent).not.toContain(TRACE_B);
    expectOnlyReads(requests, start); expectNoAnswer(); expectReadDisabled();
  });

  it.each([null, '', 'bad-id', '11111111-1111-1111-8111-111111111111', `${TRACE_A}, ${TRACE_B}`])(
    '状态失败头%s无效时不借用成功创建或错误正文编号', async trace => {
      const requests = install(request => pathname(request) === WORKFLOWS_PATH ? traced(createdWorkflow(), 201)
        : pathname(request) === WORKFLOW_PATH ? traced({ detail: PRIVATE, trace_id: TRACE_A }, 503, trace) : undefined);
      await open(); await create(); const start = requests.length; await readStatus(); expectNoTrace(); expectNoAnswer();
      expectOnlyReads(requests, start);
    });

  it('重读立即清旧编号，成功状态变化提示不使用成功响应编号', async () => {
    const pending = deferred(); let reads = 0;
    install(request => pathname(request) === WORKFLOW_PATH ? ++reads === 1 ? failed() : pending.promise : undefined);
    await open(); await create(); await readStatus(); expectTrace(stateRegion());
    await readStatus(); expectNoTrace();
    await resolve(pending, traced(savedWorkflow())); expectNoTrace(); expectNoAnswer();
    expect(stateRegion().textContent).toContain('当前任务记录已变化');
  });

  it('取消后的旧状态失败不覆盖下一次失败编号', async () => {
    const old = deferred(); let reads = 0;
    install(request => pathname(request) === WORKFLOW_PATH ? ++reads === 1 ? old.promise : failed() : undefined);
    await open(); await create(); await readStatus();
    await click(screen.getByRole('button', { name: '停止读取状态' })); expectNoTrace();
    await readStatus(); expectTrace(stateRegion()); await resolve(old, failed(TRACE_A));
    expectTrace(stateRegion()); expect(document.body.textContent).not.toContain(TRACE_A);
  });

  it('200后的状态校验失败不显示成功编号', async () => {
    install(request => pathname(request) === WORKFLOW_PATH ? traced({ invalid: PRIVATE }) : undefined);
    await open(); await create(); await readStatus(); expectNoTrace(); expectNoAnswer(); expectReadDisabled();
  });

  it.each(['network', 'timeout'] as const)('状态%s没有可用响应编号', async kind => {
    install(request => {
      if (pathname(request) !== WORKFLOW_PATH) return undefined;
      if (kind === 'network') return Promise.reject(new TypeError('controlled network failure'));
      return new Promise<Response>((_resolve, reject) => request.signal.addEventListener('abort',
        () => reject(new DOMException('controlled cancellation', 'AbortError'))));
    });
    await open(); await create(); await readStatus(); if (kind === 'timeout') await advance(10_001);
    expectNoTrace(); expectReadDisabled(); expect(stateRegion().textContent).toContain('工作流当前状态未确认');
  });

  it.each(['back', 'logout-failed', 'expiry'] as const)('%s清除已展示的状态编号', async action => {
    install(request => pathname(request) === WORKFLOW_PATH ? failed()
      : pathname(request) === LOGOUT ? failed(TRACE_A) : undefined);
    await open(); await create(); await readStatus(); expectTrace(stateRegion());
    if (action === 'back') await click(screen.getByRole('button', { name: '返回文档列表' }));
    else if (action === 'logout-failed') await click(screen.getByRole('button', { name: '退出登录' }));
    else await advance(1_800_000);
    expectNoTrace(); expectNoAnswer();
  });

  it('状态401清会话，不投影认证失败编号', async () => {
    install(request => pathname(request) === WORKFLOW_PATH ? failed(TRACE_B, 401) : undefined);
    await open(); await create(); await readStatus(); expectNoTrace(); expectNoAnswer();
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();
  });

  it.each(['cancel', 'back', 'logout-failed', 'expiry'] as const)('状态等待%s后迟到编号不复活', async action => {
    const pending = deferred();
    install(request => pathname(request) === WORKFLOW_PATH ? pending.promise
      : pathname(request) === LOGOUT ? failed(TRACE_A) : undefined);
    await open(); await create(); await readStatus();
    if (action === 'cancel') await click(screen.getByRole('button', { name: '停止读取状态' }));
    else if (action === 'back') await click(screen.getByRole('button', { name: '返回文档列表' }));
    else if (action === 'logout-failed') await click(screen.getByRole('button', { name: '退出登录' }));
    else await advance(1_800_000);
    await resolve(pending, failed()); expectNoTrace(); expectNoAnswer();
  });

  it('读取失败阻止恢复，重读匹配成功清空提示后才重新开放确认', async () => {
    let reads = 0;
    const requests = install(request => pathname(request) === WORKFLOW_EXECUTE_PATH
      ? jsonResponse(workflowExecution(failedWorkflow('generate')))
      : pathname(request) === WORKFLOW_PATH ? ++reads === 1 ? failed() : traced(failedWorkflow('generate')) : undefined);
    await open(); await create(); await click(screen.getByRole('button', { name: '执行工作流一次' }));
    await readStatus(); expectTrace(stateRegion());
    expect(screen.queryByRole('button', { name: '确认并恢复生成一次' })).toBeNull();
    await readStatus(); expectNoTrace();
    expect((screen.getByRole('button', { name: '确认并恢复生成一次' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('checkbox')).toBeTruthy(); expect(posts(requests)).toHaveLength(1);
  });

  it.each([WORKFLOW_PATH, DOCUMENT_PATH, CHUNKS_PATH, OTHER_DOCUMENT_PATH, OTHER_CHUNKS_PATH])(
    '保存答案%s失败只在答案区显示实际失败GET编号', async target => {
      let workflowReads = 0;
      const requests = install(request => {
        const path = pathname(request);
        if (path === WORKFLOW_PATH) return ++workflowReads === 1 || path !== target ? traced(savedWorkflow()) : failed();
        if (path === target) return failed();
        return undefined;
      });
      await prepare(); const start = requests.length; await readAnswer();
      expectTrace(savedRegion()); expect(stateRegion().textContent).not.toContain(TRACE_B);
      expect(document.body.textContent).not.toContain(TRACE_A); expectNoAnswer(); expectReadDisabled();
      expectOnlyReads(requests, start);
    });

  it.each([null, '', 'bad-id', '11111111-1111-1111-8111-111111111111', `${TRACE_A}, ${TRACE_B}`])(
    '保存答案末来源头%s无效时不借用成功任务编号', async trace => {
      install(request => pathname(request) === WORKFLOW_PATH ? traced(savedWorkflow())
        : pathname(request) === OTHER_CHUNKS_PATH ? traced({ detail: PRIVATE, trace_id: TRACE_A }, 503, trace) : undefined);
      await prepare(); await readAnswer(); expectNoTrace(); expectNoAnswer(); expectReadDisabled();
    });

  it('全部GET成功时不展示编号；末来源200但原文不匹配时也不借用编号', async () => {
    let chunkReads = 0;
    install(request => pathname(request) === WORKFLOW_PATH ? traced(savedWorkflow())
      : pathname(request) === OTHER_CHUNKS_PATH ? traced(++chunkReads === 1 ? savedChunks(OTHER_DOCUMENT_ID) : []) : undefined);
    await prepare(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER); expectNoTrace();
    await readAnswer(); expectNoAnswer(); expectNoTrace(); expectReadDisabled();
  });

  it('保存答案工作流200坏JSON不展示该响应编号', async () => {
    let reads = 0;
    install(request => pathname(request) === WORKFLOW_PATH && ++reads === 2
      ? new Response('{', { status: 200, headers: { 'X-Trace-Id': TRACE_A } }) : undefined);
    await prepare(); await readAnswer(); expectNoAnswer(); expectNoTrace(); expectReadDisabled();
  });

  it('读取新状态立即清除来源旧编号，成功后需要单独再次查看答案', async () => {
    let sourceReads = 0;
    install(request => pathname(request) === OTHER_CHUNKS_PATH && ++sourceReads === 1 ? failed() : undefined);
    await prepare(); await readAnswer(); expectTrace(savedRegion());
    await readStatus(); expectNoTrace(); expectNoAnswer(); await readAnswer();
    expect(savedRegion().textContent).toContain(ANSWER); expectNoTrace();
  });

  it.each(['network', 'timeout'] as const)('保存答案来源%s不补造编号', async kind => {
    install(request => {
      if (pathname(request) !== OTHER_CHUNKS_PATH) return undefined;
      if (kind === 'network') return Promise.reject(new TypeError('controlled network failure'));
      return new Promise<Response>((_resolve, reject) => request.signal.addEventListener('abort',
        () => reject(new DOMException('controlled cancellation', 'AbortError'))));
    });
    await prepare(); await readAnswer(); if (kind === 'timeout') await advance(10_001);
    expectNoTrace(); expectNoAnswer(); expectReadDisabled();
    expect(savedRegion().textContent).toContain('已保存答案尚未核对通过');
  });

  it.each([WORKFLOW_PATH, OTHER_CHUNKS_PATH])('保存答案%s的401清会话及编号', async target => {
    let reads = 0;
    install(request => pathname(request) === WORKFLOW_PATH && ++reads === 2 && target === WORKFLOW_PATH
      || pathname(request) === OTHER_CHUNKS_PATH && target === OTHER_CHUNKS_PATH ? failed(TRACE_B, 401) : undefined);
    await prepare(); await readAnswer(); expectNoAnswer(); expectNoTrace();
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();
  });

  it.each(['cancel', 'back', 'logout-failed', 'expiry'] as const)('%s使末来源迟到失败编号失效', async action => {
    const pending = deferred();
    const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH ? pending.promise
      : pathname(request) === LOGOUT ? failed(TRACE_A) : undefined);
    await prepare(); await readAnswer();
    if (action === 'cancel') await click(screen.getByRole('button', { name: '停止查看答案' }));
    else if (action === 'back') await click(screen.getByRole('button', { name: '返回文档列表' }));
    else if (action === 'logout-failed') await click(screen.getByRole('button', { name: '退出登录' }));
    else await advance(1_800_000);
    await resolve(pending, failed()); expectNoTrace(); expectNoAnswer(); expect(posts(requests)).toHaveLength(1);
  });

  it('已显示来源错误时退出失败清旧编号，也不展示退出请求编号', async () => {
    install(request => pathname(request) === OTHER_CHUNKS_PATH ? failed()
      : pathname(request) === LOGOUT ? failed(TRACE_A) : undefined);
    await prepare(); await readAnswer(); expectTrace(savedRegion());
    await click(screen.getByRole('button', { name: '退出登录' })); expectNoTrace(); expectNoAnswer();
  });

  it('创建成功及执行失败响应不接入本轮只读编号展示', async () => {
    install(request => pathname(request) === WORKFLOWS_PATH ? traced(createdWorkflow(), 201)
      : pathname(request) === WORKFLOW_EXECUTE_PATH ? failed() : undefined);
    await open(); await create(); await click(screen.getByRole('button', { name: '执行工作流一次' })); expectNoTrace();
  });

  it('创建失败响应也不通过只读提示展示编号', async () => {
    install(request => pathname(request) === WORKFLOWS_PATH ? failed() : undefined);
    await open(); await create(); expectNoTrace(); expectNoAnswer();
  });
});
