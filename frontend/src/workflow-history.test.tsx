import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { USER_ID, WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, OTHER_DOCUMENT_ID,
  DOCUMENT_PATH, OTHER_DOCUMENT_PATH, QUESTION, ANSWER, LONG_QUOTE, OTHER_QUOTE, PRIVATE,
  documentResponse, jsonResponse } from './conversation-test-fixtures';
import { WORKFLOW_ID, WORKFLOWS_PATH, WORKFLOW_PATH, completedWorkflow,
  createdWorkflow, failedWorkflow, runningWorkflow } from './workflow-test-fixtures';

const RESTORE = '/api/v1/auth/web/session', LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces', KNOWLEDGE = `${SPACES}/${WORKSPACE_ID}/knowledge-bases`;
const DOCUMENTS = `${KNOWLEDGE}/${KNOWLEDGE_BASE_ID}/documents`;
const CHUNKS_PATH = `${DOCUMENT_PATH}/chunks`, OTHER_CHUNKS_PATH = `${OTHER_DOCUMENT_PATH}/chunks`;
const SECOND_ID = '66666666-6666-6666-6666-666666666666', SECOND_QUESTION = '另一条历史任务的问题';
type Route = (request: Request) => Response | Promise<Response> | undefined;
function pathname(request: Request) { return new URL(request.url).pathname; }
function deferred() {
  let resolve!: (response: Response) => void;
  return { promise: new Promise<Response>(done => { resolve = done; }), resolve: (response: Response) => resolve(response) };
}
function savedWorkflow(question = QUESTION, id = WORKFLOW_ID) {
  const value = { ...completedWorkflow(question), workflow_id: id };
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
function summary(value: ReturnType<typeof createdWorkflow> = savedWorkflow(), index = 0) {
  return { workflow_id: value.workflow_id, workspace_id: value.workspace_id, knowledge_base_id: value.knowledge_base_id,
    question: value.question, strategy: value.strategy, max_context_characters: value.max_context_characters,
    status: value.status, next_step: value.next_step, version: value.version, attempt_count: value.attempt_count,
    error_code: value.error_code, retryable: value.retryable,
    created_at: new Date(Date.parse('2026-10-18T01:00:00Z') - index * 1000).toISOString(),
    updated_at: '2026-10-18T02:00:00Z' };
}
function historyPage(offset = 0, count = 20) {
  return Array.from({ length: count }, (_, index) => {
    const position = offset + index;
    return position === 0 ? summary() : summary({ ...createdWorkflow(`分页历史问题${position + 1}`),
      workflow_id: `11111111-1111-1111-1111-${String(position + 1).padStart(12, '0')}` }, position);
  });
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
      case WORKFLOWS_PATH: return jsonResponse([summary()]);
      case WORKFLOW_PATH: return jsonResponse(savedWorkflow());
      case `${WORKFLOWS_PATH}/${SECOND_ID}`: return jsonResponse(savedWorkflow(SECOND_QUESTION, SECOND_ID));
      default: throw new Error('Unexpected workflow-history mock path');
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
  await click(screen.getByRole('button', { name: '工作流记录' }));
}
async function list() { await click(screen.getByRole('button', { name: '读取最近工作流' })); }
async function select(question = QUESTION) { await click(screen.getByRole('button', { name: `查看工作流：${question}` })); }
async function readStatus() { await click(screen.getByRole('button', { name: '重新读取选中任务状态' })); }
async function readAnswer() { await click(screen.getByRole('button', { name: '查看已保存答案' })); }
async function prepare() { await open(); await list(); await select(); }
function sources(requests: Request[]) { return requests.filter(item => [DOCUMENT_PATH, OTHER_DOCUMENT_PATH, CHUNKS_PATH, OTHER_CHUNKS_PATH].includes(pathname(item))); }
function historyRequests(requests: Request[]) { return requests.filter(item => pathname(item).startsWith(WORKFLOWS_PATH)); }
function listQueries(requests: Request[]) {
  return requests.filter(item => pathname(item) === WORKFLOWS_PATH)
    .map(item => new URL(item.url).searchParams.toString());
}
function pageButton(name: '上一页' | '下一页' | '重新读取本页') {
  return screen.getByRole('button', { name }) as HTMLButtonElement;
}
function savedRegion() { return screen.getByRole('region', { name: '历史任务已保存答案（只读）' }); }
function expectNoAnswer() {
  for (const value of [ANSWER, LONG_QUOTE, OTHER_QUOTE, PRIVATE]) expect(document.body.textContent).not.toContain(value);
}
function expectReadDisabled() { expect((screen.getByRole('button', { name: '查看已保存答案' }) as HTMLButtonElement).disabled).toBe(true); }
function expectReadOnly(requests: Request[]) {
  expect(requests.filter(item => item.method !== 'GET' && pathname(item) !== LOGOUT)).toHaveLength(0);
  expect(screen.queryByRole('button', { name: '创建工作流' })).toBeNull();
  expect(screen.queryByRole('button', { name: '执行工作流一次' })).toBeNull();
  expect(screen.queryByRole('button', { name: '确认并恢复生成一次' })).toBeNull();
}

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2030-01-01T00:00:00Z')); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('独立工作流记录页', () => {
  it('入口不自动读取或写入，手动从最新20条开始并说明按创建时间排列的分页边界', async () => {
    const requests = install(); await open(); expect(historyRequests(requests)).toHaveLength(0);
    expect(screen.getByRole('heading', { name: '工作流记录' })).toBeTruthy();
    expect(document.body.textContent).toContain('知识库成员共享');
    expect(document.body.textContent).toContain('按创建时间排列');
    expect(document.body.textContent).toContain('跨页可能出现重复或遗漏');
    await list();
    const request = historyRequests(requests)[0];
    expect(new URL(request.url).searchParams.toString()).toBe('limit=20&offset=0');
    expect(screen.getByRole('button', { name: `查看工作流：${QUESTION}` })).toBeTruthy();
    expect(sources(requests)).toHaveLength(0); expectNoAnswer(); expectReadOnly(requests);
    const count = requests.length; await advance(30_000); expect(requests).toHaveLength(count);
  });

  it.each(['pending', 'failed', 'completed'] as const)('%s历史任务的详情和重新读取仅展示元数据，不开放写操作或自动来源GET', async status => {
    const value = status === 'pending' ? createdWorkflow() : status === 'failed' ? failedWorkflow('generate') : savedWorkflow();
    const requests = install(request => pathname(request) === WORKFLOWS_PATH ? jsonResponse([summary(value)])
      : pathname(request) === WORKFLOW_PATH ? jsonResponse(value) : undefined);
    await prepare(); expectNoAnswer(); expectReadOnly(requests); expect(sources(requests)).toHaveLength(0);
    const count = requests.length; await readStatus();
    expect(requests.slice(count).map(pathname)).toEqual([WORKFLOW_PATH]); expectNoAnswer();
    expect((screen.getByRole('button', { name: '查看已保存答案' }) as HTMLButtonElement).disabled).toBe(status !== 'completed');
    expectReadOnly(requests);
  });

  it('保留混合检索历史行但没有打开按钮，其余vector记录可独立打开', async () => {
    const hybrid = { ...summary(savedWorkflow(SECOND_QUESTION, SECOND_ID), 1), strategy: 'hybrid' };
    const requests = install(request => pathname(request) === WORKFLOWS_PATH ? jsonResponse([summary(), hybrid]) : undefined);
    await open(); await list(); expect(document.body.textContent).toContain(SECOND_QUESTION);
    expect(document.body.textContent).toContain('本页暂不支持打开混合检索历史');
    expect(screen.queryByRole('button', { name: `查看工作流：${SECOND_QUESTION}` })).toBeNull();
    await select(); expect(historyRequests(requests).map(pathname)).toEqual([WORKFLOWS_PATH, WORKFLOW_PATH]);
    expectReadOnly(requests);
  });

  it('空列表有独立空态，没有残留的选中详情或答案', async () => {
    const requests = install(request => pathname(request) === WORKFLOWS_PATH ? jsonResponse([]) : undefined);
    await open(); await list(); expect(document.body.textContent).toContain('本次读取未发现工作流记录');
    expect(screen.queryByRole('heading', { name: '选中任务状态' })).toBeNull(); expectReadOnly(requests);
  });

  it('满20条提示可能有更早记录，下一页必须由用户手动读取', async () => {
    const values = Array.from({ length: 20 }, (_, index) => summary({ ...createdWorkflow(`历史问题${index}`),
      workflow_id: `11111111-1111-1111-1111-${String(index + 1).padStart(12, '0')}` }, index));
    const requests = install(request => pathname(request) === WORKFLOWS_PATH ? jsonResponse(values) : undefined);
    await open(); await list(); expect(screen.getAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(20);
    expect(document.body.textContent).toContain('可能还有更早的记录');
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(false);
    expect(historyRequests(requests)).toHaveLength(1); expectReadOnly(requests);
  });

  it('单独查看保存答案核对所有检查点来源，全部通过前不展示正文；双击不会重复读取', async () => {
    const pending = deferred(); const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH ? pending.promise : undefined);
    await prepare(); const count = requests.length; await readAnswer(); expectNoAnswer();
    const button = screen.getByRole('button', { name: '正在核对已保存答案…' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true); await click(button); await click(button);
    expect(requests.slice(count).map(pathname)).toEqual([WORKFLOW_PATH, DOCUMENT_PATH, CHUNKS_PATH, OTHER_DOCUMENT_PATH, OTHER_CHUNKS_PATH]);
    await resolve(pending, jsonResponse(savedChunks(OTHER_DOCUMENT_ID)));
    expect(savedRegion().textContent).toContain(ANSWER); expect(savedRegion().textContent).toContain(LONG_QUOTE);
    expect(savedRegion().textContent).not.toContain(OTHER_QUOTE); expect(document.querySelector('img')).toBeNull();
    expectReadOnly(requests);
  });

  it('未引用的第二来源原文变化也整体隐藏答案，要求重新读取状态', async () => {
    const chunks = savedChunks(OTHER_DOCUMENT_ID); chunks[0].content = '变'.repeat(Array.from(chunks[0].content).length);
    const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH ? jsonResponse(chunks) : undefined);
    await prepare(); await readAnswer(); expectNoAnswer(); expectReadDisabled();
    expect(sources(requests).map(pathname)).toEqual([DOCUMENT_PATH, CHUNKS_PATH, OTHER_DOCUMENT_PATH, OTHER_CHUNKS_PATH]);
    expectReadOnly(requests);
  });

  it('列表读取中禁用按钮，双击不重复GET', async () => {
    const pending = deferred(); const requests = install(request => pathname(request) === WORKFLOWS_PATH ? pending.promise : undefined);
    await open(); await list(); const button = screen.getByRole('button', { name: '读取最近工作流' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true); await click(button); await click(button);
    expect(historyRequests(requests)).toHaveLength(1);
    await resolve(pending, jsonResponse([summary()])); expectReadOnly(requests);
  });

  it('重新读取列表立即清除选中状态及旧正文，失败后不保留旧列表', async () => {
    const pending = deferred(); let reads = 0;
    const requests = install(request => pathname(request) === WORKFLOWS_PATH && ++reads === 2 ? pending.promise : undefined);
    await prepare(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER);
    await list(); expectNoAnswer(); expect(screen.queryByRole('heading', { name: '选中任务状态' })).toBeNull();
    await resolve(pending, jsonResponse({ detail: PRIVATE }, 503));
    expectNoAnswer(); expect(screen.queryByRole('button', { name: `查看工作流：${QUESTION}` })).toBeNull();
    expectReadOnly(requests);
  });

  it('选择另一条任务立即清除旧正文，不会把第一条答案放到第二条任务下', async () => {
    const pending = deferred();
    const requests = install(request => pathname(request) === WORKFLOWS_PATH
      ? jsonResponse([summary(), summary(savedWorkflow(SECOND_QUESTION, SECOND_ID), 1)])
      : pathname(request) === `${WORKFLOWS_PATH}/${SECOND_ID}` ? pending.promise : undefined);
    await prepare(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER);
    await select(SECOND_QUESTION); expectNoAnswer(); expectReadDisabled();
    await resolve(pending, jsonResponse(savedWorkflow(SECOND_QUESTION, SECOND_ID)));
    expectNoAnswer(); expect((screen.getByRole('button', { name: '查看已保存答案' }) as HTMLButtonElement).disabled).toBe(false);
    expectReadOnly(requests);
  });

  it('重新读取选中状态立即清正文，失败不能沿用旧completed门控', async () => {
    let reads = 0; const requests = install(request => pathname(request) === WORKFLOW_PATH && ++reads === 3
      ? jsonResponse({ detail: PRIVATE }, 503) : undefined);
    await prepare(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER);
    await readStatus(); expectNoAnswer(); expectReadDisabled(); expectReadOnly(requests);
  });

  it.each(['list', 'detail', 'source'] as const)('%s返回401清保护内容并回登录页', async target => {
    const requests = install(request => (pathname(request) === WORKFLOWS_PATH && target === 'list'
      || pathname(request) === WORKFLOW_PATH && target === 'detail'
      || pathname(request) === OTHER_CHUNKS_PATH && target === 'source') ? jsonResponse({ detail: PRIVATE }, 401) : undefined);
    await open(); await list();
    if (target !== 'list') await select();
    if (target === 'source') await readAnswer();
    expectNoAnswer(); expect(screen.getByRole('button', { name: '登录' })).toBeTruthy(); expectReadOnly(requests);
  });

  it.each(['list', 'detail'] as const)('%s返回404不保留旧详情或来源正文', async target => {
    let lists = 0, details = 0;
    const requests = install(request => (pathname(request) === WORKFLOWS_PATH && ++lists === 2 && target === 'list'
      || pathname(request) === WORKFLOW_PATH && ++details === 3 && target === 'detail')
      ? jsonResponse({ detail: PRIVATE }, 404) : undefined);
    await prepare(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER);
    if (target === 'list') await list(); else await readStatus();
    expectNoAnswer(); expectReadOnly(requests);
    if (target === 'detail') expectReadDisabled();
  });

  it.each(['list', 'detail', 'answer'] as const)('停止%s读取后迟到响应不能恢复内容或继续来源读取', async target => {
    const pending = deferred(); let details = 0;
    const requests = install(request => (pathname(request) === WORKFLOWS_PATH && target === 'list'
      || pathname(request) === WORKFLOW_PATH && ++details === (target === 'detail' ? 1 : 2) && target !== 'list')
      ? pending.promise : undefined);
    await open(); await list();
    if (target !== 'list') await select();
    if (target === 'answer') await readAnswer();
    await click(screen.getByRole('button', { name: target === 'list' ? '停止读取工作流记录'
      : target === 'detail' ? '停止读取任务状态' : '停止查看答案' }));
    await resolve(pending, jsonResponse(target === 'list' ? [summary()] : savedWorkflow()));
    expectNoAnswer(); expect(sources(requests)).toHaveLength(0); expectReadOnly(requests);
    if (target !== 'list') expectReadDisabled();
  });

  it.each(['back', 'logout', 'expiry'] as const)('末来源等待中%s清正文并隔离迟到来源结果', async action => {
    const pending = deferred(); const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH ? pending.promise : undefined);
    await prepare(); await readAnswer(); expectNoAnswer();
    if (action === 'back') await click(screen.getByRole('button', { name: '返回文档列表' }));
    else if (action === 'logout') await click(screen.getByRole('button', { name: '退出登录' }));
    else await advance(1_800_000);
    await resolve(pending, jsonResponse(savedChunks(OTHER_DOCUMENT_ID))); expectNoAnswer(); expectReadOnly(requests);
    if (action !== 'back') expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();
    else expect(screen.getByRole('heading', { name: '文档' })).toBeTruthy();
  });

  it('退出等待中隐藏历史内容，退出失败也不能接纳迟到来源，需重新读取状态', async () => {
    const source = deferred(), logout = deferred();
    const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH ? source.promise
      : pathname(request) === LOGOUT ? logout.promise : undefined);
    await prepare(); await readAnswer(); await click(screen.getByRole('button', { name: '退出登录' }));
    expectNoAnswer(); expect(screen.queryByRole('button', { name: `查看工作流：${QUESTION}` })).toBeNull();
    await resolve(source, jsonResponse(savedChunks(OTHER_DOCUMENT_ID))); expectNoAnswer();
    await resolve(logout, jsonResponse({ detail: PRIVATE }, 502)); expectNoAnswer(); expectReadDisabled();
    expectReadOnly(requests);
  });

  it('重新挂载App后通过重新读取列表找到同一任务，不依赖本地存储或恢复写请求', async () => {
    const get = vi.spyOn(Storage.prototype, 'getItem'), set = vi.spyOn(Storage.prototype, 'setItem');
    const requests = install(); await prepare(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER);
    cleanup(); await open(); expectNoAnswer();
    expect(screen.queryByRole('button', { name: `查看工作流：${QUESTION}` })).toBeNull();
    await list(); await select(); expectNoAnswer(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER);
    expect(get).not.toHaveBeenCalled(); expect(set).not.toHaveBeenCalled(); expectReadOnly(requests);
  });
});

const TRACE_A = 'a0e0b901-2b0d-4ae0-812a-bab2cf9e7b01';
const TRACE_B = 'b1f1c012-3c1e-4bf1-923b-cbc3dfa08c12';
function traced(payload: unknown, status = 503, trace?: string) {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (trace !== undefined) headers.set('X-Trace-Id', trace);
  return new Response(JSON.stringify(payload), { status, headers });
}
function tracedFailure(trace: string | undefined = TRACE_A, status = 503) {
  return traced({ detail: PRIVATE, trace_id: TRACE_B }, status, trace);
}
function expectNoTrace() {
  for (const value of ['排错编号：', TRACE_A, TRACE_B]) expect(document.body.textContent).not.toContain(value);
}
function expectTrace(value: string) {
  expect(screen.getByText(value).tagName).toBe('CODE');
  expect(document.body.textContent).not.toContain(PRIVATE);
}

describe('工作流列表与状态读取失败的排错编号', () => {
  it.each(['list', 'detail'] as const)('%s错误在所属区域展示编号，不读取正文、不发写请求或自动重试', async target => {
    const requests = install(request => pathname(request) === (target === 'list' ? WORKFLOWS_PATH : WORKFLOW_PATH)
      ? tracedFailure() : undefined);
    await open(); await list(); if (target === 'detail') await select();
    expectTrace(TRACE_A); expectNoAnswer();
    const region = screen.getByRole('region', { name: target === 'list' ? '工作流历史列表' : '选中任务状态' });
    expect(region.textContent).toContain(TRACE_A);
    const count = requests.length; await advance(30_000); expect(requests).toHaveLength(count);
    expectReadOnly(requests);
    for (const request of requests) expect(request.headers.has('X-Trace-Id')).toBe(false);
  });

  it.each([undefined, 'not-a-uuid', TRACE_B.replace('-4bf1-', '-1bf1-'), `${TRACE_A}, ${TRACE_B}`])(
    '详情失败头%s无效时不沿用成功列表的编号或错误正文编号', async trace => {
      const requests = install(request => pathname(request) === WORKFLOWS_PATH ? traced([summary()], 200, TRACE_A)
        : pathname(request) === WORKFLOW_PATH ? traced({ detail: PRIVATE, trace_id: TRACE_B }, 404, trace) : undefined);
      await prepare(); expectNoTrace(); expectNoAnswer(); expectReadOnly(requests);
      expect(screen.getAllByRole('alert').length).toBeGreaterThan(0);
    });

  it('有效详情失败只用该GET编号，不显示成功列表的编号', async () => {
    const requests = install(request => pathname(request) === WORKFLOWS_PATH ? traced([summary()], 200, TRACE_A)
      : pathname(request) === WORKFLOW_PATH ? tracedFailure(TRACE_B, 404) : undefined);
    await prepare(); expectTrace(TRACE_B); expect(document.body.textContent).not.toContain(TRACE_A);
    expectReadOnly(requests);
  });

  it('重新读取列表立即清旧错误及编号，成功后不显示成功响应编号', async () => {
    const pending = deferred(); let reads = 0;
    const requests = install(request => pathname(request) === WORKFLOWS_PATH
      ? ++reads === 1 ? tracedFailure() : pending.promise : undefined);
    await open(); await list(); expectTrace(TRACE_A);
    await list(); expectNoTrace();
    await resolve(pending, traced([], 200, TRACE_B)); expectNoTrace();
    expect(document.body.textContent).toContain('本次读取未发现工作流记录'); expectReadOnly(requests);
  });

  it('切换任务清除旧错误编号，随后错误属于新选中任务', async () => {
    const pending = deferred();
    const requests = install(request => pathname(request) === WORKFLOWS_PATH
      ? jsonResponse([summary(), summary(savedWorkflow(SECOND_QUESTION, SECOND_ID), 1)])
      : pathname(request) === WORKFLOW_PATH ? tracedFailure()
        : pathname(request) === `${WORKFLOWS_PATH}/${SECOND_ID}` ? pending.promise : undefined);
    await prepare(); expectTrace(TRACE_A);
    await select(SECOND_QUESTION); expectNoTrace();
    await resolve(pending, tracedFailure(TRACE_B)); expectTrace(TRACE_B);
    expect(document.body.textContent).not.toContain(TRACE_A); expectReadOnly(requests);
    expect(screen.getByRole('region', { name: '选中任务状态' }).textContent).toContain(SECOND_QUESTION);
  });

  it.each(['list', 'detail'] as const)('停止%s后重读，旧请求迟到编号不能覆盖新错误', async target => {
    const old = deferred(); let reads = 0;
    const requests = install(request => pathname(request) === (target === 'list' ? WORKFLOWS_PATH : WORKFLOW_PATH)
      ? ++reads === 1 ? old.promise : tracedFailure() : undefined);
    await open(); await list(); if (target === 'detail') await select();
    await click(screen.getByRole('button', { name: target === 'list' ? '停止读取工作流记录' : '停止读取任务状态' }));
    expectNoTrace();
    if (target === 'list') await list(); else await readStatus();
    expectTrace(TRACE_A);
    await resolve(old, tracedFailure(TRACE_B)); expectTrace(TRACE_A);
    expect(document.body.textContent).not.toContain(TRACE_B); expectReadOnly(requests);
  });

  it('重新读取状态清旧编号，200后本地校验失败不展示该成功HTTP编号', async () => {
    let reads = 0;
    const requests = install(request => pathname(request) === WORKFLOW_PATH
      ? ++reads === 1 ? tracedFailure() : traced({ private: PRIVATE }, 200, TRACE_B) : undefined);
    await prepare(); expectTrace(TRACE_A); await readStatus(); expectNoTrace();
    expectReadDisabled(); expectReadOnly(requests); expectNoAnswer();
  });

  it.each(['list', 'detail'] as const)('%s失败后退出未确认，旧读取与退出编号都不显示', async target => {
    const requests = install(request => pathname(request) === (target === 'list' ? WORKFLOWS_PATH : WORKFLOW_PATH)
      ? tracedFailure() : pathname(request) === LOGOUT ? tracedFailure(TRACE_B) : undefined);
    await open(); await list(); if (target === 'detail') await select(); expectTrace(TRACE_A);
    await click(screen.getByRole('button', { name: '退出登录' })); expectNoTrace();
    expect(document.body.textContent).toContain('退出尚未确认'); expectReadOnly(requests);
  });

  it.each(['list', 'detail'] as const)('%s的401不把编号带到登录页面', async target => {
    const requests = install(request => pathname(request) === (target === 'list' ? WORKFLOWS_PATH : WORKFLOW_PATH)
      ? tracedFailure(TRACE_A, 401) : undefined);
    await open(); await list(); if (target === 'detail') await select();
    expectNoTrace(); expect(screen.getByRole('button', { name: '登录' })).toBeTruthy(); expectReadOnly(requests);
  });

  it.each(['back', 'expiry'] as const)('%s清除旧错误及编号', async action => {
    const requests = install(request => pathname(request) === WORKFLOW_PATH ? tracedFailure() : undefined);
    await prepare(); expectTrace(TRACE_A);
    if (action === 'back') {
      await click(screen.getByRole('button', { name: '返回文档列表' }));
      await click(screen.getByRole('button', { name: '工作流记录' }));
    } else await advance(1_800_000);
    expectNoTrace(); expectReadOnly(requests);
  });

  it('保存答案查看失败编号只属于答案区，不混入成功列表或状态编号', async () => {
    let reads = 0;
    const requests = install(request => pathname(request) === WORKFLOW_PATH
      ? ++reads === 1 ? traced(savedWorkflow(), 200, TRACE_A) : tracedFailure(TRACE_B) : undefined);
    await prepare(); expectNoTrace(); await readAnswer(); expectTrace(TRACE_B);
    expect(savedRegion().textContent).toContain(TRACE_B);
    expect(screen.getByRole('region', { name: '工作流历史列表' }).textContent).not.toContain(TRACE_B);
    expect(screen.getByRole('region', { name: '选中任务状态' }).textContent).not.toContain(TRACE_B);
    expect(document.body.textContent).not.toContain(TRACE_A);
    expectNoAnswer(); expectReadOnly(requests);
  });
});

function sourcePayload(path: string): unknown {
  switch (path) {
    case DOCUMENT_PATH: return documentResponse();
    case CHUNKS_PATH: return savedChunks();
    case OTHER_DOCUMENT_PATH: return documentResponse(OTHER_DOCUMENT_ID);
    case OTHER_CHUNKS_PATH: return savedChunks(OTHER_DOCUMENT_ID);
    default: return undefined;
  }
}

describe('历史页已保存答案核验失败编号', () => {
  it.each([WORKFLOW_PATH, DOCUMENT_PATH, CHUNKS_PATH, OTHER_DOCUMENT_PATH, OTHER_CHUNKS_PATH])(
    '%s失败只在保存答案区显示自身编号，隐藏整份答案且不自动重读', async failedPath => {
      let metadataReads = 0;
      const requests = install(request => {
        const path = pathname(request);
        if (path === WORKFLOW_PATH) {
          if (++metadataReads === 2 && path === failedPath) return tracedFailure(TRACE_B);
          return traced(savedWorkflow(), 200, TRACE_A);
        }
        const payload = sourcePayload(path);
        if (payload !== undefined) return path === failedPath ? tracedFailure(TRACE_B) : traced(payload, 200, TRACE_A);
      });
      await prepare(); expectNoTrace(); await readAnswer();
      expectTrace(TRACE_B); expect(document.body.textContent).not.toContain(TRACE_A);
      expect(savedRegion().textContent).toContain(TRACE_B); expectNoAnswer(); expectReadDisabled();
      expect(screen.getByRole('region', { name: '选中任务状态' }).textContent).not.toContain(TRACE_B);
      const count = requests.length; await advance(30_000); expect(requests).toHaveLength(count);
      expectReadOnly(requests);
      for (const request of requests) expect(request.headers.has('X-Trace-Id')).toBe(false);
    });

  it.each([undefined, 'not-a-uuid', 'b1f1c012-3c1e-1bf1-923b-cbc3dfa08c12', `${TRACE_A}, ${TRACE_B}`])(
    '末来源失败头%s无效时不回退成功任务或来源编号', async trace => {
      const requests = install(request => {
        const path = pathname(request);
        if (path === WORKFLOW_PATH) return traced(savedWorkflow(), 200, TRACE_A);
        if (path === OTHER_CHUNKS_PATH) return traced({ detail: PRIVATE, trace_id: TRACE_B }, 503, trace);
        const payload = sourcePayload(path);
        if (payload !== undefined) return traced(payload, 200, TRACE_A);
      });
      await prepare(); await readAnswer(); expectNoTrace(); expectNoAnswer(); expectReadDisabled(); expectReadOnly(requests);
    });

  it('末来源200但原文变化只显示未核验提示，不展示成功HTTP编号', async () => {
    const changed = savedChunks(OTHER_DOCUMENT_ID);
    changed[0].content = '变'.repeat(Array.from(changed[0].content).length);
    const requests = install(request => {
      const path = pathname(request);
      if (path === WORKFLOW_PATH) return traced(savedWorkflow(), 200, TRACE_A);
      if (path === OTHER_CHUNKS_PATH) return traced(changed, 200, TRACE_B);
      const payload = sourcePayload(path); if (payload !== undefined) return traced(payload, 200, TRACE_A);
    });
    await prepare(); await readAnswer(); expectNoTrace(); expectNoAnswer(); expectReadDisabled(); expectReadOnly(requests);
  });

  it.each(['list', 'select', 'state'] as const)('%s操作清除旧保存答案错误及编号', async action => {
    const requests = install(request => pathname(request) === WORKFLOWS_PATH
      ? jsonResponse([summary(), summary(savedWorkflow(SECOND_QUESTION, SECOND_ID), 1)])
      : pathname(request) === OTHER_CHUNKS_PATH ? tracedFailure() : undefined);
    await prepare(); await readAnswer(); expectTrace(TRACE_A);
    if (action === 'list') await list(); else if (action === 'select') await select(SECOND_QUESTION); else await readStatus();
    expectNoTrace(); expectNoAnswer(); expectReadOnly(requests);
  });

  it('重新读状态后才可再次查看，旧编号不会进入新失败', async () => {
    const pending = deferred(); let reads = 0;
    const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH
      ? ++reads === 1 ? tracedFailure() : pending.promise : undefined);
    await prepare(); await readAnswer(); expectTrace(TRACE_A); expectReadDisabled();
    await readStatus(); expectNoTrace(); await readAnswer(); expectNoTrace();
    await resolve(pending, tracedFailure(TRACE_B)); expectTrace(TRACE_B);
    expect(document.body.textContent).not.toContain(TRACE_A); expectNoAnswer(); expectReadOnly(requests);
  });

  it.each(['cancel', 'back', 'logout-failed', 'expiry'] as const)('%s后末来源迟到失败不能恢复编号或正文', async action => {
    const pending = deferred();
    const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH ? pending.promise
      : pathname(request) === LOGOUT && action === 'logout-failed' ? tracedFailure(TRACE_A) : undefined);
    await prepare(); await readAnswer(); expectNoAnswer();
    if (action === 'cancel') await click(screen.getByRole('button', { name: '停止查看答案' }));
    else if (action === 'back') await click(screen.getByRole('button', { name: '返回文档列表' }));
    else if (action === 'logout-failed') await click(screen.getByRole('button', { name: '退出登录' }));
    else await advance(1_800_000);
    await resolve(pending, tracedFailure(TRACE_B)); expectNoTrace(); expectNoAnswer(); expectReadOnly(requests);
    expect(requests.find(request => pathname(request) === OTHER_CHUNKS_PATH)?.signal.aborted).toBe(true);
  });

  it('已展示来源错误后退出失败，清除来源编号且不展示退出编号', async () => {
    const requests = install(request => pathname(request) === OTHER_CHUNKS_PATH ? tracedFailure()
      : pathname(request) === LOGOUT ? tracedFailure(TRACE_B) : undefined);
    await prepare(); await readAnswer(); expectTrace(TRACE_A);
    await click(screen.getByRole('button', { name: '退出登录' }));
    expectNoTrace(); expectNoAnswer(); expect(document.body.textContent).toContain('退出尚未确认'); expectReadOnly(requests);
  });

  it.each([WORKFLOW_PATH, DOCUMENT_PATH, CHUNKS_PATH])('%s的401清身份，不在登录页面展示核验编号', async failedPath => {
    let reads = 0;
    const requests = install(request => {
      const path = pathname(request);
      if (path === WORKFLOW_PATH && ++reads === 1) return undefined;
      if (path === failedPath) return tracedFailure(TRACE_B, 401);
    });
    await prepare(); await readAnswer(); expectNoTrace(); expectNoAnswer();
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy(); expectReadOnly(requests);
  });
});

describe('工作流历史手动翻页', () => {
  it('初次不翻页；按20条读取下一页与空尾页，再返回上一页或最新记录', async () => {
    const requests = install(request => {
      if (pathname(request) !== WORKFLOWS_PATH) return undefined;
      const offset = Number(new URL(request.url).searchParams.get('offset'));
      return jsonResponse(offset === 40 ? [] : historyPage(offset));
    });
    await open();
    expect(pageButton('上一页').disabled).toBe(true);
    expect(pageButton('下一页').disabled).toBe(true);
    expect(screen.queryByRole('button', { name: '重新读取本页' })).toBeNull();
    await click(pageButton('下一页')); expect(listQueries(requests)).toEqual([]);

    await list();
    expect(pageButton('上一页').disabled).toBe(true);
    expect(pageButton('下一页').disabled).toBe(false);
    await click(pageButton('下一页'));
    expect(screen.getAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(20);
    expect(screen.getByRole('button', { name: '查看工作流：分页历史问题21' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: `查看工作流：${QUESTION}` })).toBeNull();
    expect(pageButton('上一页').disabled).toBe(false);
    expect(pageButton('下一页').disabled).toBe(false);
    expect(pageButton('重新读取本页').disabled).toBe(false);

    await click(pageButton('下一页'));
    expect(screen.queryAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(0);
    expect(pageButton('上一页').disabled).toBe(false);
    expect(pageButton('下一页').disabled).toBe(true);
    await click(pageButton('上一页'));
    expect(screen.getByRole('button', { name: '查看工作流：分页历史问题21' })).toBeTruthy();
    await list();
    expect(screen.getByRole('button', { name: `查看工作流：${QUESTION}` })).toBeTruthy();
    expect(pageButton('上一页').disabled).toBe(true);
    expect(screen.queryByRole('button', { name: '重新读取本页' })).toBeNull();
    expect(listQueries(requests)).toEqual([
      'limit=20&offset=0', 'limit=20&offset=20', 'limit=20&offset=40',
      'limit=20&offset=20', 'limit=20&offset=0',
    ]);
    const count = requests.length; await advance(30_000); expect(requests).toHaveLength(count);
    expectReadOnly(requests);
  });

  it.each([0, 1, 19])('首页返回%s条时禁用下一页，不额外探测或猜测总数', async count => {
    const requests = install(request => pathname(request) === WORKFLOWS_PATH
      ? jsonResponse(historyPage(0, count)) : undefined);
    await open(); await list();
    expect(screen.queryAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(count);
    expect(pageButton('上一页').disabled).toBe(true);
    expect(pageButton('下一页').disabled).toBe(true);
    await click(pageButton('下一页'));
    expect(listQueries(requests)).toEqual(['limit=20&offset=0']); expectReadOnly(requests);
  });

  it('翻页开始立即清除旧列表、选中详情与已显示答案，等待期间不允许重复翻页', async () => {
    const pending = deferred();
    const requests = install(request => {
      if (pathname(request) !== WORKFLOWS_PATH) return undefined;
      return new URL(request.url).searchParams.get('offset') === '20'
        ? pending.promise : jsonResponse(historyPage());
    });
    await prepare(); await readAnswer(); expect(savedRegion().textContent).toContain(ANSWER);
    await click(pageButton('下一页'));
    expectNoAnswer(); expectNoTrace();
    expect(screen.queryAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(0);
    expect(screen.queryByRole('heading', { name: '选中任务状态' })).toBeNull();
    expect(screen.queryByRole('region', { name: '历史任务已保存答案（只读）' })).toBeNull();
    for (const name of ['上一页', '下一页', '重新读取本页'] as const) {
      expect(pageButton(name).disabled).toBe(true);
      await click(pageButton(name));
    }
    expect(listQueries(requests)).toEqual(['limit=20&offset=0', 'limit=20&offset=20']);
    await resolve(pending, jsonResponse(historyPage(20, 1)));
    expect(screen.getByRole('button', { name: '查看工作流：分页历史问题21' })).toBeTruthy();
    expectNoAnswer(); expectReadOnly(requests);
  });

  it.each(['detail', 'saved-answer'] as const)('翻页清除旧%s失败编号，成功新页不展示成功响应编号', async target => {
    const pending = deferred();
    const requests = install(request => {
      const path = pathname(request);
      if (path === WORKFLOWS_PATH) return new URL(request.url).searchParams.get('offset') === '20'
        ? pending.promise : jsonResponse(historyPage());
      if ((target === 'detail' && path === WORKFLOW_PATH)
          || (target === 'saved-answer' && path === OTHER_CHUNKS_PATH)) return tracedFailure(TRACE_A);
    });
    await prepare(); if (target === 'saved-answer') await readAnswer(); expectTrace(TRACE_A);
    await click(pageButton('下一页')); expectNoTrace(); expectNoAnswer();
    expect(screen.queryByRole('heading', { name: '选中任务状态' })).toBeNull();
    await resolve(pending, traced(historyPage(20, 1), 200, TRACE_B));
    expectNoTrace(); expectNoAnswer(); expectReadOnly(requests);
  });

  it('下一页失败保留目标页，可重读offset20且不自动返回首页，重读立即清旧编号', async () => {
    const retry = deferred(); let pageReads = 0;
    const requests = install(request => {
      if (pathname(request) !== WORKFLOWS_PATH) return undefined;
      if (new URL(request.url).searchParams.get('offset') === '20') {
        return ++pageReads === 1 ? tracedFailure(TRACE_A) : retry.promise;
      }
      return jsonResponse(historyPage());
    });
    await open(); await list(); await click(pageButton('下一页')); expectTrace(TRACE_A);
    expect(screen.queryAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(0);
    expect(pageButton('上一页').disabled).toBe(false);
    expect(pageButton('下一页').disabled).toBe(true);
    expect(pageButton('重新读取本页').disabled).toBe(false);
    expect(listQueries(requests)).toEqual(['limit=20&offset=0', 'limit=20&offset=20']);
    await click(pageButton('重新读取本页')); expectNoTrace();
    expect(listQueries(requests)).toEqual(['limit=20&offset=0', 'limit=20&offset=20', 'limit=20&offset=20']);
    await resolve(retry, traced(historyPage(20, 1), 200, TRACE_B));
    expect(screen.getByRole('button', { name: '查看工作流：分页历史问题21' })).toBeTruthy();
    expect(pageButton('下一页').disabled).toBe(true); expectNoTrace(); expectReadOnly(requests);
  });

  it.each(['success', 'failure'] as const)('取消后仍重读目标页，旧%s迟到不能覆盖随后offset40的错误', async late => {
    const old = deferred(); let pageReads = 0;
    const requests = install(request => {
      if (pathname(request) !== WORKFLOWS_PATH) return undefined;
      const offset = new URL(request.url).searchParams.get('offset');
      if (offset === '20') return ++pageReads === 1 ? old.promise : jsonResponse(historyPage(20));
      if (offset === '40') return tracedFailure(TRACE_A);
      return jsonResponse(historyPage());
    });
    await open(); await list(); await click(pageButton('下一页'));
    const cancelled = historyRequests(requests).at(-1)!;
    await click(screen.getByRole('button', { name: '停止读取工作流记录' }));
    expect(cancelled.signal.aborted).toBe(true);
    expectNoTrace(); expectNoAnswer();
    expect(pageButton('上一页').disabled).toBe(false);
    expect(pageButton('重新读取本页').disabled).toBe(false);
    await click(pageButton('重新读取本页'));
    expect(screen.getByRole('button', { name: '查看工作流：分页历史问题21' })).toBeTruthy();
    await click(pageButton('下一页')); expectTrace(TRACE_A);
    await resolve(old, late === 'success' ? jsonResponse(historyPage(20)) : tracedFailure(TRACE_B));
    expectTrace(TRACE_A); expect(document.body.textContent).not.toContain(TRACE_B);
    expect(screen.queryAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(0);
    expect(listQueries(requests)).toEqual([
      'limit=20&offset=0', 'limit=20&offset=20', 'limit=20&offset=20', 'limit=20&offset=40',
    ]);
    expectNoAnswer(); expectReadOnly(requests);
  });

  it('翻页等待中返回并重新进入从首页未读取状态开始，迟到页不能恢复旧编号', async () => {
    const old = deferred();
    const requests = install(request => pathname(request) === WORKFLOWS_PATH
      ? new URL(request.url).searchParams.get('offset') === '20' ? old.promise : jsonResponse(historyPage()) : undefined);
    await open(); await list(); await click(pageButton('下一页'));
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    await click(screen.getByRole('button', { name: '工作流记录' }));
    expect(pageButton('上一页').disabled).toBe(true);
    expect(pageButton('下一页').disabled).toBe(true);
    expect(screen.queryByRole('button', { name: '重新读取本页' })).toBeNull();
    await resolve(old, tracedFailure(TRACE_B)); expectNoTrace(); expectNoAnswer();
    expect(screen.queryAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(0);
    await list();
    expect(listQueries(requests)).toEqual(['limit=20&offset=0', 'limit=20&offset=20', 'limit=20&offset=0']);
    expectReadOnly(requests);
  });

  it('下一页401清会话与页码，重新挂载恢复登录后只从首页手动读取', async () => {
    const requests = install(request => pathname(request) === WORKFLOWS_PATH
      ? new URL(request.url).searchParams.get('offset') === '20' ? tracedFailure(TRACE_A, 401)
        : jsonResponse(historyPage()) : undefined);
    await open(); await list(); await click(pageButton('下一页'));
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy(); expectNoTrace(); expectNoAnswer();
    cleanup(); await open();
    expect(pageButton('上一页').disabled).toBe(true);
    expect(pageButton('下一页').disabled).toBe(true);
    expect(listQueries(requests)).toHaveLength(2);
    await list();
    expect(listQueries(requests)).toEqual(['limit=20&offset=0', 'limit=20&offset=20', 'limit=20&offset=0']);
    expectReadOnly(requests);
  });

  it('翻页中退出失败仍保留目标页，旧响应不复活且只重读本页', async () => {
    const old = deferred(); let pageReads = 0;
    const requests = install(request => {
      if (pathname(request) === LOGOUT) return tracedFailure(TRACE_A);
      if (pathname(request) !== WORKFLOWS_PATH) return undefined;
      if (new URL(request.url).searchParams.get('offset') === '20') {
        return ++pageReads === 1 ? old.promise : jsonResponse(historyPage(20, 1));
      }
      return jsonResponse(historyPage());
    });
    await open(); await list(); await click(pageButton('下一页'));
    await click(screen.getByRole('button', { name: '退出登录' }));
    expect(document.body.textContent).toContain('退出尚未确认'); expectNoTrace();
    await resolve(old, tracedFailure(TRACE_B)); expectNoTrace(); expectNoAnswer();
    expect(screen.queryAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(0);
    await click(pageButton('重新读取本页'));
    expect(screen.getByRole('button', { name: '查看工作流：分页历史问题21' })).toBeTruthy();
    expect(listQueries(requests)).toEqual(['limit=20&offset=0', 'limit=20&offset=20', 'limit=20&offset=20']);
    expectReadOnly(requests);
  });
});

const STATUS_FILTERS = ['pending', 'running', 'failed', 'completed'] as const;
type StatusFilter = 'all' | typeof STATUS_FILTERS[number];
function statusSelector() { return screen.getByRole('combobox', { name: '任务状态' }) as HTMLSelectElement; }
async function changeStatus(statusFilter: StatusFilter) {
  await act(async () => {
    fireEvent.change(statusSelector(), { target: { value: statusFilter } });
    await vi.advanceTimersByTimeAsync(0);
  });
}
function statusPage(status: typeof STATUS_FILTERS[number], offset = 0, count = 20) {
  const state = status === 'pending' ? createdWorkflow() : status === 'running' ? runningWorkflow()
    : status === 'failed' ? failedWorkflow() : savedWorkflow();
  return historyPage(offset, count).map((row, index) => summary({ ...state,
    workflow_id: row.workflow_id, question: row.question }, offset + index));
}
function expectUnreadFirstPage() {
  expect(pageButton('上一页').disabled).toBe(true);
  expect(pageButton('下一页').disabled).toBe(true);
  expect(screen.queryByRole('button', { name: '重新读取本页' })).toBeNull();
  expect(screen.queryAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(0);
  expect(screen.queryByRole('heading', { name: '选中任务状态' })).toBeNull();
  expect(screen.queryByRole('region', { name: '历史任务已保存答案（只读）' })).toBeNull();
  expectNoAnswer(); expectNoTrace();
}

describe('历史页手动按任务状态筛选', () => {
  it('默认全部，只提供四种任务状态；进入和切换条件都不自动读取', async () => {
    const requests = install(); await open();
    expect(statusSelector().value).toBe('all'); expect(statusSelector().disabled).toBe(false);
    expect(Array.from(statusSelector().options, option => option.value)).toEqual(['all', ...STATUS_FILTERS]);
    expect(document.body.textContent).toContain('筛选状态：');
    expectUnreadFirstPage(); expect(historyRequests(requests)).toHaveLength(0);
    await changeStatus('failed'); await changeStatus('all');
    expect(statusSelector().value).toBe('all'); expectUnreadFirstPage();
    expect(historyRequests(requests)).toHaveLength(0);
    await list();
    expect(listQueries(requests)).toEqual(['limit=20&offset=0']); expectReadOnly(requests);
  });

  it.each(STATUS_FILTERS)('选择%s后等待手动读取，GET同时携带状态与首页偏移', async status => {
    const requests = install(request => pathname(request) === WORKFLOWS_PATH
      ? jsonResponse(statusPage(status, 0, 1)) : undefined);
    await open(); const before = requests.length;
    await changeStatus(status);
    expect(statusSelector().value).toBe(status); expectUnreadFirstPage();
    expect(requests).toHaveLength(before);
    await list();
    expect(listQueries(requests)).toEqual([`limit=20&offset=0&status=${status}`]);
    expect(screen.getByRole('button', { name: `查看工作流：${QUESTION}` })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '选中任务状态' })).toBeNull();
    expect(sources(requests)).toHaveLength(0);
    const count = requests.length; await advance(30_000); expect(requests).toHaveLength(count);
    expectReadOnly(requests);
  });

  it('翻页、空尾页返回和读取最近工作流都保留状态；满20条不猜测总数', async () => {
    const requests = install(request => {
      if (pathname(request) !== WORKFLOWS_PATH) return undefined;
      const offset = Number(new URL(request.url).searchParams.get('offset'));
      return jsonResponse(statusPage('failed', offset, offset === 40 ? 0 : 20));
    });
    await open(); await changeStatus('failed'); await list();
    expect(pageButton('下一页').disabled).toBe(false);
    await click(pageButton('下一页'));
    expect(statusSelector().value).toBe('failed');
    expect(screen.getByRole('button', { name: '查看工作流：分页历史问题21' })).toBeTruthy();
    await click(pageButton('下一页'));
    expect(statusSelector().value).toBe('failed'); expect(pageButton('下一页').disabled).toBe(true);
    expect(pageButton('上一页').disabled).toBe(false);
    expect(screen.queryAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(0);
    await click(pageButton('上一页')); await list();
    expect(statusSelector().value).toBe('failed'); expect(pageButton('上一页').disabled).toBe(true);
    expect(listQueries(requests)).toEqual([
      'limit=20&offset=0&status=failed', 'limit=20&offset=20&status=failed',
      'limit=20&offset=40&status=failed', 'limit=20&offset=20&status=failed', 'limit=20&offset=0&status=failed',
    ]);
    const count = requests.length; await changeStatus('all'); expectUnreadFirstPage();
    expect(requests).toHaveLength(count); await list();
    expect(statusSelector().value).toBe('all'); expect(listQueries(requests).at(-1)).toBe('limit=20&offset=0');
    expectReadOnly(requests);
  });

  it('筛选后的第二页失败保留条件及目标页，本页重读不回退全部状态或首页', async () => {
    const retry = deferred(); let reads = 0;
    const requests = install(request => {
      if (pathname(request) !== WORKFLOWS_PATH) return undefined;
      if (new URL(request.url).searchParams.get('offset') === '20') {
        return ++reads === 1 ? tracedFailure(TRACE_A) : retry.promise;
      }
      return jsonResponse(statusPage('running'));
    });
    await open(); await changeStatus('running'); await list(); await click(pageButton('下一页'));
    expectTrace(TRACE_A); expect(statusSelector().value).toBe('running');
    expect(pageButton('上一页').disabled).toBe(false); expect(pageButton('下一页').disabled).toBe(true);
    await click(pageButton('重新读取本页')); expectNoTrace(); expect(statusSelector().disabled).toBe(true);
    await resolve(retry, traced(statusPage('running', 20, 1), 200, TRACE_B));
    expectNoTrace(); expect(statusSelector().value).toBe('running');
    expect(screen.getByRole('button', { name: '查看工作流：分页历史问题21' })).toBeTruthy();
    expect(listQueries(requests)).toEqual([
      'limit=20&offset=0&status=running', 'limit=20&offset=20&status=running', 'limit=20&offset=20&status=running',
    ]);
    expectReadOnly(requests);
  });

  it('条件没有匹配记录时展示当前读取空态，不自动改为全部或猜测后面有记录', async () => {
    const requests = install(request => pathname(request) === WORKFLOWS_PATH ? jsonResponse([]) : undefined);
    await open(); await changeStatus('completed'); await list();
    expect(statusSelector().value).toBe('completed');
    expect(document.body.textContent).toContain('本次读取未发现工作流记录');
    expect(pageButton('上一页').disabled).toBe(true); expect(pageButton('下一页').disabled).toBe(true);
    expectNoAnswer(); expectNoTrace();
    const count = requests.length; await advance(30_000); expect(requests).toHaveLength(count);
    expect(listQueries(requests)).toEqual(['limit=20&offset=0&status=completed']); expectReadOnly(requests);
  });

  it('从第二页切换条件立即清除已展示的任务详情与答案，页码归零且不读取新条件', async () => {
    const requests = install(request => {
      if (pathname(request) !== WORKFLOWS_PATH) return undefined;
      const offset = Number(new URL(request.url).searchParams.get('offset'));
      return jsonResponse(offset === 20 ? [summary()] : historyPage());
    });
    await open(); await list(); await click(pageButton('下一页')); await select(); await readAnswer();
    expect(savedRegion().textContent).toContain(ANSWER);
    const count = requests.length; await changeStatus('failed');
    expect(statusSelector().value).toBe('failed'); expectUnreadFirstPage();
    expect(requests).toHaveLength(count); expectReadOnly(requests);
  });

  it.each(['list', 'detail', 'saved-answer'] as const)('切换条件清除旧%s错误与编号；未手动读取前保持空白', async target => {
    const requests = install(request => {
      const path = pathname(request);
      if ((target === 'list' && path === WORKFLOWS_PATH)
          || (target === 'detail' && path === WORKFLOW_PATH)
          || (target === 'saved-answer' && path === OTHER_CHUNKS_PATH)) return tracedFailure(TRACE_A);
    });
    await open(); await list();
    if (target !== 'list') await select();
    if (target === 'saved-answer') await readAnswer();
    expectTrace(TRACE_A); const count = requests.length;
    await changeStatus('pending'); expectUnreadFirstPage();
    expect(requests).toHaveLength(count); expectReadOnly(requests);
  });

  it('筛选响应混入其他合法状态时整页不展示，不本地删行且不显示成功编号', async () => {
    const mixed = [summary(failedWorkflow()), summary(savedWorkflow(SECOND_QUESTION, SECOND_ID), 1)];
    const requests = install(request => pathname(request) === WORKFLOWS_PATH ? traced(mixed, 200, TRACE_A) : undefined);
    await open(); await changeStatus('failed'); await list();
    expect(screen.queryAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(0);
    expect(screen.getAllByRole('alert').length).toBeGreaterThan(0);
    expectNoTrace(); expectNoAnswer(); expect(sources(requests)).toHaveLength(0);
    expect(listQueries(requests)).toEqual(['limit=20&offset=0&status=failed']); expectReadOnly(requests);
  });

  it.each(['list', 'detail', 'answer'] as const)('%s读取中禁用筛选条件，取消后可切换且迟到成功不能恢复旧列表或答案', async target => {
    const old = deferred();
    const requests = install(request => (pathname(request) === WORKFLOWS_PATH && target === 'list'
      || pathname(request) === WORKFLOW_PATH && target === 'detail'
      || pathname(request) === OTHER_CHUNKS_PATH && target === 'answer') ? old.promise : undefined);
    await open(); await list();
    if (target !== 'list') await select();
    if (target === 'answer') await readAnswer();
    expect(statusSelector().disabled).toBe(true);
    await click(screen.getByRole('button', { name: target === 'list' ? '停止读取工作流记录'
      : target === 'detail' ? '停止读取任务状态' : '停止查看答案' }));
    expect(statusSelector().disabled).toBe(false);
    await changeStatus('failed'); expectUnreadFirstPage();
    const count = requests.length;
    await resolve(old, jsonResponse(target === 'list' ? [summary()] : target === 'detail'
      ? savedWorkflow() : savedChunks(OTHER_DOCUMENT_ID)));
    expect(statusSelector().value).toBe('failed'); expectUnreadFirstPage();
    expect(requests).toHaveLength(count); expectReadOnly(requests);
  });

  it.each(['success', 'failure'] as const)('取消旧条件后切换并手动读取，新错误不被旧%s迟到结果覆盖', async late => {
    const old = deferred();
    const requests = install(request => {
      if (pathname(request) !== WORKFLOWS_PATH) return undefined;
      return new URL(request.url).searchParams.get('status') === 'failed' ? tracedFailure(TRACE_A) : old.promise;
    });
    await open(); await list(); const cancelled = historyRequests(requests).at(-1)!;
    await click(screen.getByRole('button', { name: '停止读取工作流记录' }));
    expect(cancelled.signal.aborted).toBe(true);
    await changeStatus('failed'); expectUnreadFirstPage(); await list(); expectTrace(TRACE_A);
    await resolve(old, late === 'success' ? jsonResponse([summary()]) : tracedFailure(TRACE_B));
    expectTrace(TRACE_A); expect(document.body.textContent).not.toContain(TRACE_B);
    expect(statusSelector().value).toBe('failed');
    expect(screen.queryAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(0);
    expect(listQueries(requests)).toEqual(['limit=20&offset=0', 'limit=20&offset=0&status=failed']);
    expectReadOnly(requests);
  });

  it('返回文档列表后重入恢复全部、首页与未读取状态，不继承上次筛选', async () => {
    const requests = install(request => pathname(request) === WORKFLOWS_PATH
      ? jsonResponse(statusPage('pending', 0, 1)) : undefined);
    await open(); await changeStatus('pending'); await list();
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    await click(screen.getByRole('button', { name: '工作流记录' }));
    expect(statusSelector().value).toBe('all'); expectUnreadFirstPage();
    expect(listQueries(requests)).toEqual(['limit=20&offset=0&status=pending']); expectReadOnly(requests);
  });

  it.each(['401', 'expiry', 'logout'] as const)('带条件读取中%s清身份与条件，迟到错误不在登录页恢复编号', async action => {
    const old = deferred();
    const requests = install(request => pathname(request) === WORKFLOWS_PATH
      ? action === '401' ? tracedFailure(TRACE_A, 401) : old.promise : undefined);
    await open(); await changeStatus('failed'); await list();
    if (action === 'expiry') await advance(1_800_000);
    else if (action === 'logout') await click(screen.getByRole('button', { name: '退出登录' }));
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy(); expectNoAnswer(); expectNoTrace();
    if (action !== '401') await resolve(old, tracedFailure(TRACE_B));
    expectNoAnswer(); expectNoTrace();
    cleanup(); await open();
    expect(statusSelector().value).toBe('all'); expectUnreadFirstPage();
    expect(listQueries(requests)).toEqual(['limit=20&offset=0&status=failed']); expectReadOnly(requests);
  });

  it('带条件翻页时退出失败仍保留条件与目标页，迟到响应不能替代手动本页重读', async () => {
    const old = deferred(); let secondPageReads = 0;
    const requests = install(request => {
      if (pathname(request) === LOGOUT) return tracedFailure(TRACE_A);
      if (pathname(request) !== WORKFLOWS_PATH) return undefined;
      if (new URL(request.url).searchParams.get('offset') === '20') {
        return ++secondPageReads === 1 ? old.promise : jsonResponse(statusPage('failed', 20, 1));
      }
      return jsonResponse(statusPage('failed'));
    });
    await open(); await changeStatus('failed'); await list(); await click(pageButton('下一页'));
    await click(screen.getByRole('button', { name: '退出登录' }));
    expect(document.body.textContent).toContain('退出尚未确认'); expectNoTrace();
    expect(statusSelector().value).toBe('failed');
    await resolve(old, tracedFailure(TRACE_B)); expectNoTrace(); expectNoAnswer();
    await click(pageButton('重新读取本页'));
    expect(statusSelector().value).toBe('failed');
    expect(screen.getByRole('button', { name: '查看工作流：分页历史问题21' })).toBeTruthy();
    expect(listQueries(requests)).toEqual([
      'limit=20&offset=0&status=failed', 'limit=20&offset=20&status=failed', 'limit=20&offset=20&status=failed',
    ]);
    expectReadOnly(requests);
  });
});
