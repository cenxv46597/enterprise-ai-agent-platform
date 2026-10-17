import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { USER_ID, WORKSPACE_ID, KNOWLEDGE_BASE_ID, OTHER_KNOWLEDGE_BASE_ID,
  DOCUMENT_PATH, OTHER_DOCUMENT_PATH, OTHER_DOCUMENT_ID, QUESTION, ANSWER, ABSTENTION, LONG_QUOTE, OTHER_QUOTE, PRIVATE,
  documentResponse, jsonResponse } from './conversation-test-fixtures';
import { WORKFLOWS_PATH, WORKFLOW_PATH, WORKFLOW_EXECUTE_PATH, CREATION_REQUEST_ID, WORKFLOW_EXECUTION_REQUEST_ID,
  createdWorkflow, completedWorkflow, abstainedWorkflow, failedWorkflow, runningWorkflow, workflowExecution } from './workflow-test-fixtures';

const RESTORE = '/api/v1/auth/web/session', LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces', KNOWLEDGE = `${SPACES}/${WORKSPACE_ID}/knowledge-bases`;
const DOCUMENTS = `${KNOWLEDGE}/${KNOWLEDGE_BASE_ID}/documents`;
type Route = (request: Request) => Response | Promise<Response> | undefined;
function deferred() {
  let resolve!: (response: Response) => void;
  return { promise: new Promise<Response>(done => { resolve = done; }), resolve: (response: Response) => resolve(response) };
}
function pathname(request: Request) { return new URL(request.url).pathname; }
function install(route: Route = () => undefined) {
  const requests: Request[] = [];
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const request = new Request(new URL(String(input), 'http://localhost'), init); requests.push(request);
    const response = route(request); if (response !== undefined) return response;
    switch (pathname(request)) {
      case RESTORE: return jsonResponse({ user: { id: USER_ID, username: 'demo_a' }, expires_at: Date.now() / 1000 + 1800 });
      case LOGOUT: return new Response(null, { status: 204 });
      case SPACES: return jsonResponse([{ id: WORKSPACE_ID, name: '甲空间' }]);
      case KNOWLEDGE: return jsonResponse([{ id: KNOWLEDGE_BASE_ID, workspace_id: WORKSPACE_ID, name: '甲知识库' },
        { id: OTHER_KNOWLEDGE_BASE_ID, workspace_id: WORKSPACE_ID, name: '乙知识库' }]);
      case DOCUMENTS: return jsonResponse([{ ...documentResponse(), status: 'parsed' }]);
      case `${KNOWLEDGE}/${OTHER_KNOWLEDGE_BASE_ID}/documents`: return jsonResponse([]);
      case DOCUMENT_PATH: return jsonResponse(documentResponse());
      case OTHER_DOCUMENT_PATH: return jsonResponse(documentResponse(OTHER_DOCUMENT_ID));
      case WORKFLOWS_PATH: return jsonResponse(createdWorkflow(), 201);
      case WORKFLOW_EXECUTE_PATH: return jsonResponse(workflowExecution());
      case WORKFLOW_PATH: return jsonResponse(completedWorkflow());
      default: throw new Error('Unexpected mocked workflow path');
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
async function create(question = QUESTION) {
  await act(async () => { fireEvent.change(screen.getByLabelText('工作流问题'), { target: { value: question } }); });
  await click(screen.getByRole('button', { name: '创建工作流' }));
}
async function execute() { await click(screen.getByRole('button', { name: '执行工作流一次' })); }
function posts(requests: Request[], url = WORKFLOW_EXECUTE_PATH) {
  return requests.filter(item => item.method === 'POST' && pathname(item) === url);
}
function expectNoAnswer() {
  expect(document.body.textContent).not.toContain(ANSWER);
  expect(document.body.textContent).not.toContain(LONG_QUOTE);
  expect(document.body.textContent).not.toContain(OTHER_QUOTE);
  expect(document.body.textContent).not.toContain(PRIVATE);
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
  vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce(CREATION_REQUEST_ID).mockReturnValue(WORKFLOW_EXECUTION_REQUEST_ID) });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('独立问答工作流页第一小步', () => {
  it('第一页零份ready也可进入；进入不创建或执行，输入不触发POST', async () => {
    const requests = install(); await open();
    expect(screen.getByRole('heading', { level: 1, name: '问答工作流' })).toBeTruthy();
    expect(posts(requests, WORKFLOWS_PATH)).toHaveLength(0);
    expect(posts(requests)).toHaveLength(0);
    await act(async () => { fireEvent.change(screen.getByLabelText('工作流问题'), { target: { value: QUESTION } }); });
    expect(posts(requests, WORKFLOWS_PATH)).toHaveLength(0);
    expect(requests.some(item => /\/(answers|search|conversations)$/.test(pathname(item)))).toBe(false);
  });

  it('空白或超长问题本地拒绝；创建确认后才开放一次执行，使用两个独立请求编号', async () => {
    const requests = install(); await open();
    for (const value of ['', ' \n ', '😀'.repeat(2001)]) await create(value);
    expect(posts(requests, WORKFLOWS_PATH)).toHaveLength(0);
    await create(` \n${QUESTION}\t`);
    expect((screen.getByLabelText('工作流问题') as HTMLTextAreaElement).disabled).toBe(true);
    expect(posts(requests, WORKFLOWS_PATH)).toHaveLength(1); expect(posts(requests)).toHaveLength(0);
    expect(await posts(requests, WORKFLOWS_PATH)[0].clone().json()).toEqual({ request_id: CREATION_REQUEST_ID,
      question: QUESTION, strategy: 'vector' });
    await execute();
    expect(await posts(requests)[0].clone().json()).toEqual({ request_id: WORKFLOW_EXECUTION_REQUEST_ID,
      expected_version: 0, recover_interrupted: false });
    expect(posts(requests)).toHaveLength(1);
    expect(document.body.textContent).toContain(QUESTION);
    expect(document.body.textContent).toContain(ANSWER);
  });

  it('创建等待中不能重复点击；创建取消的迟到201不开放执行或再次创建', async () => {
    const pending = deferred(); const requests = install(request => pathname(request) === WORKFLOWS_PATH ? pending.promise : undefined);
    await open(); await create();
    expect(posts(requests, WORKFLOWS_PATH)).toHaveLength(1);
    const createButton = screen.getByRole('button', { name: /正在创建/ }) as HTMLButtonElement;
    expect(createButton.disabled).toBe(true); await click(createButton);
    await click(screen.getByRole('button', { name: '停止等待创建结果' }));
    await resolve(pending, jsonResponse(createdWorkflow(), 201));
    expect(document.body.textContent).toContain('工作流创建结果未确认');
    const button = screen.queryByRole('button', { name: '执行工作流一次' }) as HTMLButtonElement | null;
    if (button) expect(button.disabled).toBe(true);
    expect(posts(requests)).toHaveLength(0); expect(posts(requests, WORKFLOWS_PATH)).toHaveLength(1);
  });

  it('创建502保留原问题快照，不再次创建或自动执行', async () => {
    const requests = install(request => pathname(request) === WORKFLOWS_PATH ? jsonResponse({ detail: PRIVATE }, 502) : undefined);
    await open(); await create();
    expect(document.body.textContent).toContain(QUESTION);
    expect(document.body.textContent).toContain('工作流创建结果未确认');
    expect(document.body.textContent).not.toContain(PRIVATE);
    await advance(30_000);
    expect(posts(requests, WORKFLOWS_PATH)).toHaveLength(1); expect(posts(requests)).toHaveLength(0);
  });

  it('全部检查点来源核对完成前不展示答案，完整引文以纯文本保留', async () => {
    const pending = deferred(); const requests = install(request => pathname(request) === OTHER_DOCUMENT_PATH ? pending.promise : undefined);
    await open(); await create(); await execute(); expectNoAnswer();
    const execution = screen.getByRole('button', { name: '正在执行工作流…' }) as HTMLButtonElement;
    expect(execution.disabled).toBe(true); await click(execution);
    expect(posts(requests)).toHaveLength(1);
    await resolve(pending, jsonResponse(documentResponse(OTHER_DOCUMENT_ID)));
    for (const value of [ANSWER, LONG_QUOTE, OTHER_QUOTE]) expect(document.body.textContent).toContain(value);
    expect(document.querySelector('img')).toBeNull();
  });

  it('正常弃答是已完成回执，不冒充技术失败且不查询来源', async () => {
    const requests = install(request => pathname(request) === WORKFLOW_EXECUTE_PATH ? jsonResponse(workflowExecution(abstainedWorkflow())) : undefined);
    await open(); await create(); await execute();
    expect(document.body.textContent).toContain(ABSTENTION);
    expect(document.body.textContent).not.toContain('执行结果未确认');
    expect(requests.some(item => [DOCUMENT_PATH, OTHER_DOCUMENT_PATH].includes(pathname(item)))).toBe(false);
  });

  it.each(['retrieve', 'generate'] as const)('HTTP200的%s失败如实展示，保留重试标志但不提供恢复', async stage => {
    const requests = install(request => pathname(request) === WORKFLOW_EXECUTE_PATH ? jsonResponse(workflowExecution(failedWorkflow(stage))) : undefined);
    await open(); await create(); await execute();
    expect(document.body.textContent).toContain('失败');
    expect(document.body.textContent).not.toContain('执行结果未确认');
    expect(screen.queryByRole('button', { name: /恢复|重试/ })).toBeNull();
    const executeButton = screen.queryByRole('button', { name: '执行工作流一次' }) as HTMLButtonElement | null;
    if (executeButton) expect(executeButton.disabled).toBe(true);
    expect(posts(requests)).toHaveLength(1); expectNoAnswer();
  });

  it.each([409, 503])('执行HTTP%s后读取completed只显示元数据，不恢复答案或解锁重发', async status => {
    const requests = install(request => pathname(request) === WORKFLOW_EXECUTE_PATH ? jsonResponse({ detail: PRIVATE }, status) : undefined);
    await open(); await create(); await execute(); expectNoAnswer();
    await click(screen.getByRole('button', { name: '读取当前工作流状态' })); expectNoAnswer();
    const execution = screen.queryByRole('button', { name: '执行工作流一次' }) as HTMLButtonElement | null;
    if (execution) { expect(execution.disabled).toBe(true); await click(execution); }
    expect(posts(requests)).toHaveLength(1);
  });

  it('执行前GET看到running即暂停写入，不接管租约或采用新版本自动执行', async () => {
    const requests = install(request => pathname(request) === WORKFLOW_PATH ? jsonResponse(runningWorkflow()) : undefined);
    await open(); await create(); await click(screen.getByRole('button', { name: '读取当前工作流状态' }));
    const button = screen.queryByRole('button', { name: '执行工作流一次' }) as HTMLButtonElement | null;
    if (button) expect(button.disabled).toBe(true);
    expect(posts(requests)).toHaveLength(0);
  });

  it.each(['cancel', 'back', 'logout', 'expiry'])('执行期间%s隔离迟到回执，不自动重发', async action => {
    const pending = deferred(); const requests = install(request => pathname(request) === WORKFLOW_EXECUTE_PATH ? pending.promise : undefined);
    await open(); await create(); await execute();
    if (action === 'cancel') await click(screen.getByRole('button', { name: '停止等待执行结果' }));
    else if (action === 'back') await click(screen.getByRole('button', { name: '返回文档列表' }));
    else if (action === 'logout') await click(screen.getByRole('button', { name: '退出登录' }));
    else await advance(1_800_000);
    await resolve(pending, jsonResponse(workflowExecution())); expectNoAnswer();
    expect(posts(requests)).toHaveLength(1);
    if (action === 'cancel') expect(document.body.textContent).toContain('工作流执行结果未确认');
  });

  it.each([401, 404])('来源%s失败整体隐藏回答，401退出登录', async status => {
    install(request => pathname(request) === OTHER_DOCUMENT_PATH ? jsonResponse({ detail: PRIVATE }, status) : undefined);
    await open(); await create(); await execute(); expectNoAnswer();
    if (status === 401) expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();
    else expect(document.body.textContent).toContain('工作流执行结果未确认');
  });

  it('GET404清除先前确认的答案；GET只读不发执行或来源请求', async () => {
    const requests = install(request => pathname(request) === WORKFLOW_PATH ? jsonResponse({ detail: PRIVATE }, 404) : undefined);
    await open(); await create(); await execute(); expect(document.body.textContent).toContain(ANSWER);
    const count = requests.length; await click(screen.getByRole('button', { name: '读取当前工作流状态' }));
    expectNoAnswer(); expect(requests.slice(count).map(pathname)).toEqual([WORKFLOW_PATH]);
    expect(posts(requests)).toHaveLength(1);
  });

  it('退出失败也保留被中断执行的未确认记录，迟到回执不能复活', async () => {
    const pending = deferred(); const requests = install(request => pathname(request) === WORKFLOW_EXECUTE_PATH ? pending.promise
      : pathname(request) === LOGOUT ? jsonResponse({ detail: PRIVATE }, 502) : undefined);
    await open(); await create(); await execute(); await click(screen.getByRole('button', { name: '退出登录' }));
    await resolve(pending, jsonResponse(workflowExecution())); expectNoAnswer();
    expect(document.body.textContent).toContain('工作流执行结果未确认'); expect(posts(requests)).toHaveLength(1);
  });

  it('执行来源等待期间停止，迟到来源成功也不显示已准备的答案', async () => {
    const pending = deferred(); const requests = install(request => pathname(request) === OTHER_DOCUMENT_PATH ? pending.promise : undefined);
    await open(); await create(); await execute(); expectNoAnswer();
    await click(screen.getByRole('button', { name: '停止等待执行结果' }));
    await resolve(pending, jsonResponse(documentResponse(OTHER_DOCUMENT_ID)));
    expectNoAnswer(); expect(posts(requests)).toHaveLength(1);
  });

  it('重新进入其他知识库的工作流页不恢复旧问题、答案、请求或创建任务', async () => {
    const requests = install(); await open(); await create(); await execute();
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    await click(screen.getByRole('button', { name: '返回知识库' }));
    await click(screen.getByRole('button', { name: '查看乙知识库的文档' }));
    await click(screen.getByRole('button', { name: '问答工作流' }));
    expect((screen.getByLabelText('工作流问题') as HTMLTextAreaElement).value).toBe('');
    expect(document.body.textContent).not.toContain(QUESTION); expectNoAnswer();
    expect(posts(requests, WORKFLOWS_PATH)).toHaveLength(1); expect(posts(requests)).toHaveLength(1);
  });

  it('执行超时后即使GET仍为初始版本，也不能重发', async () => {
    const pending = deferred(); const requests = install(request => pathname(request) === WORKFLOW_EXECUTE_PATH ? pending.promise
      : pathname(request) === WORKFLOW_PATH ? jsonResponse(createdWorkflow()) : undefined);
    await open(); await create(); await execute();
    await advance(630_000);
    expect(posts(requests)[0].signal.aborted).toBe(true);
    await resolve(pending, jsonResponse(workflowExecution())); expectNoAnswer();
    await click(screen.getByRole('button', { name: '读取当前工作流状态' }));
    expectNoAnswer(); expect((screen.getByRole('button', { name: '执行工作流一次' }) as HTMLButtonElement).disabled).toBe(true);
    expect(posts(requests)).toHaveLength(1);
  });
});
