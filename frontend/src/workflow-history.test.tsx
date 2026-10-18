import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { USER_ID, WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, OTHER_DOCUMENT_ID,
  DOCUMENT_PATH, OTHER_DOCUMENT_PATH, QUESTION, ANSWER, LONG_QUOTE, OTHER_QUOTE, PRIVATE,
  documentResponse, jsonResponse } from './conversation-test-fixtures';
import { WORKFLOW_ID, WORKFLOWS_PATH, WORKFLOW_PATH, completedWorkflow,
  createdWorkflow, failedWorkflow } from './workflow-test-fixtures';

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
  it('入口不自动读取或写入，手动按固定第一页读取最近创建20条而非全部或最近更新', async () => {
    const requests = install(); await open(); expect(historyRequests(requests)).toHaveLength(0);
    expect(screen.getByRole('heading', { name: '工作流记录' })).toBeTruthy();
    expect(document.body.textContent).toContain('知识库成员共享');
    expect(document.body.textContent).toContain('按创建时间排列');
    expect(document.body.textContent).toContain('不是全部历史记录');
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

  it('满20条提示可能有更早记录且本版不翻页', async () => {
    const values = Array.from({ length: 20 }, (_, index) => summary({ ...createdWorkflow(`历史问题${index}`),
      workflow_id: `11111111-1111-1111-1111-${String(index + 1).padStart(12, '0')}` }, index));
    const requests = install(request => pathname(request) === WORKFLOWS_PATH ? jsonResponse(values) : undefined);
    await open(); await list(); expect(screen.getAllByRole('button', { name: /^查看工作流：/ })).toHaveLength(20);
    expect(document.body.textContent).toContain('可能还有更早的记录');
    expect(document.body.textContent).toContain('本版不提供翻页'); expectReadOnly(requests);
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
