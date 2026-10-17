import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';
import {
  USER_ID, WORKSPACE_ID, KNOWLEDGE_BASE_ID, OTHER_KNOWLEDGE_BASE_ID,
  CONVERSATION_ID, REQUEST_ID, OTHER_DOCUMENT_ID,
  CONVERSATIONS_PATH, CONVERSATION_PATH, MESSAGES_PATH, DOCUMENT_PATH, OTHER_DOCUMENT_PATH,
  QUESTION, ANSWER, ABSTENTION, RULE, PRIVATE, LONG_QUOTE, OTHER_QUOTE,
  jsonResponse, createdConversation, documentResponse,
  completedReply, abstainedReply, waitingReply, billingWaitingReply, failedReply,
  CONDITION_REQUEST_ID, MONTH_REQUEST_ID, conditionReply,
  EXECUTION_REQUEST_ID, billingExecutionReply,
} from './conversation-test-fixtures';

const RESTORE = '/api/v1/auth/web/session';
const LOGOUT = '/api/v1/auth/web/logout';
const SPACES = '/api/v1/workspaces';
const KNOWLEDGE = `${SPACES}/${WORKSPACE_ID}/knowledge-bases`;
const DOCUMENTS = `${KNOWLEDGE}/${KNOWLEDGE_BASE_ID}/documents`;
const OTHER_DOCUMENTS = `${KNOWLEDGE}/${OTHER_KNOWLEDGE_BASE_ID}/documents`;

type Route = (request: Request) => Response | Promise<Response> | undefined;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(complete => { resolve = complete; });
  return { promise, resolve };
}

function sessionResponse(expiresAt = Date.now() / 1000 + 1800) {
  return jsonResponse({ user: { id: USER_ID, username: 'demo_a' }, expires_at: expiresAt });
}

function pathOf(request: Request) { return new URL(request.url).pathname; }
function creationPosts(requests: Request[]) {
  return requests.filter(request => request.method === 'POST' && pathOf(request) === CONVERSATIONS_PATH);
}
function messagePosts(requests: Request[]) {
  return requests.filter(request => request.method === 'POST' && pathOf(request) === MESSAGES_PATH);
}
function snapshotReads(requests: Request[]) {
  return requests.filter(request => request.method === 'GET' && pathOf(request) === CONVERSATION_PATH);
}
function sourceReads(requests: Request[]) {
  return requests.filter(request => request.method === 'GET'
    && [DOCUMENT_PATH, OTHER_DOCUMENT_PATH].includes(pathOf(request)));
}

// 每个请求均在此替换；迟到响应故意不随 abort 消失，以核对页面自己的失效保护。
function installApi(route: Route = () => undefined) {
  const requests: Request[] = [];
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? new Request(input, init)
      : new Request(new URL(String(input), 'http://localhost'), init);
    requests.push(request);
    const response = route(request);
    if (response !== undefined) return response;
    switch (pathOf(request)) {
      case RESTORE:
      case '/api/v1/auth/web/login': return sessionResponse();
      case LOGOUT: return new Response(null, { status: 204 });
      case SPACES: return jsonResponse([{ id: WORKSPACE_ID, name: '甲空间' }]);
      case KNOWLEDGE: return jsonResponse([
        { id: KNOWLEDGE_BASE_ID, workspace_id: WORKSPACE_ID, name: '甲知识库' },
        { id: OTHER_KNOWLEDGE_BASE_ID, workspace_id: WORKSPACE_ID, name: '乙知识库' },
      ]);
      // 文档第一页没有 ready，不代表整个知识库没有可检索资料。
      case DOCUMENTS: return jsonResponse([{ ...documentResponse(), status: 'parsed' }]);
      case OTHER_DOCUMENTS: return jsonResponse([]);
      case DOCUMENT_PATH: return jsonResponse(documentResponse());
      case OTHER_DOCUMENT_PATH: return jsonResponse(documentResponse(OTHER_DOCUMENT_ID));
      case CONVERSATIONS_PATH: return jsonResponse(createdConversation(), 201);
      case MESSAGES_PATH: return jsonResponse(completedReply());
      case CONVERSATION_PATH: return jsonResponse(createdConversation());
      default: throw new Error('Unexpected mocked endpoint');
    }
  }));
  return requests;
}

async function advance(milliseconds = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
}
async function click(element: HTMLElement) {
  await act(async () => { fireEvent.click(element); await vi.advanceTimersByTimeAsync(0); });
}
async function resolveResponse(pending: ReturnType<typeof deferred<Response>>, response: Response) {
  await act(async () => { pending.resolve(response); await vi.advanceTimersByTimeAsync(0); });
}
async function openDocuments() {
  await advance();
  await click(screen.getByRole('button', { name: '进入甲空间' }));
  await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
}
async function openConversation() {
  await openDocuments();
  await click(screen.getByRole('button', { name: '会话问答' }));
}
async function create() { await click(screen.getByRole('button', { name: '创建会话' })); }
function firstInput() { return screen.getByLabelText('首条消息') as HTMLTextAreaElement; }
async function fillMessage(value: string) {
  await act(async () => { fireEvent.change(firstInput(), { target: { value } }); });
}
async function send(value = QUESTION) {
  await fillMessage(value);
  await click(screen.getByRole('button', { name: '发送首条消息' }));
}
function expectNoPrivateOutput() { expect(document.body.textContent).not.toContain(PRIVATE); }
function expectNoResult() {
  // 未确认警示可留在结果区；这里禁止展示成已确认的答案、弃答或完整引文。
  expect(screen.queryByRole('heading', { name: '本轮回答' })).toBeNull();
  expect(screen.queryByRole('heading', { name: '证据不足，本轮弃答' })).toBeNull();
  for (const value of [ANSWER, LONG_QUOTE, OTHER_QUOTE]) expect(document.body.textContent).not.toContain(value);
}
async function expectNoSecondMessage(requests: Request[]) {
  const button = screen.queryByRole('button', { name: '发送首条消息' }) as HTMLButtonElement | null;
  if (button) {
    expect(button.disabled).toBe(true);
    await click(button);
  }
  expect(messagePosts(requests)).toHaveLength(1);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
  vi.stubGlobal('crypto', { randomUUID: vi.fn(() => REQUEST_ID) });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function supplement(value: string) {
  await act(async () => { fireEvent.change(screen.getByRole('textbox', { name: /^补充/ }), { target: { value } }); });
  await click(screen.getByRole('button', { name: '提交补充条件' }));
}

describe('逐项补充条件页面', () => {
  function setup(response?: () => Response | Promise<Response>, sourceFailure?: () => Response | Promise<Response> | undefined) {
    let posts = 0;
    vi.mocked(crypto.randomUUID).mockReturnValueOnce(REQUEST_ID)
      .mockReturnValueOnce(CONDITION_REQUEST_ID).mockReturnValueOnce(MONTH_REQUEST_ID).mockReturnValueOnce(EXECUTION_REQUEST_ID);
    return installApi(request => {
      if (pathOf(request) === MESSAGES_PATH) {
        posts += 1;
        return posts === 1 ? jsonResponse(billingWaitingReply())
          : response ? response() : posts === 4 ? jsonResponse(billingExecutionReply()) : jsonResponse(conditionReply(posts === 3));
      }
      if (posts > 1 && pathOf(request) === DOCUMENT_PATH) return sourceFailure?.();
    });
  }
  async function start() { render(<App />); await openConversation(); await create(); await send(); }

  it('按次数、月数分别手动提交；当前标记移动，ready不自动计算或解锁首问', async () => {
    const requests = setup();
    await start();
    expect(screen.getByText(/固定单位：次/)).toBeTruthy();
    await supplement('3');
    expect(document.body.textContent).toContain('上门次数：3次');
    expect(document.body.textContent).toContain('计费月数（当前待补）');
    expect(document.body.textContent).not.toContain('上门次数（当前待补）');
    expect(screen.getByLabelText('补充计费月数')).toBeTruthy();
    expect((screen.getByLabelText('补充计费月数') as HTMLInputElement).value).toBe('');
    expect(messagePosts(requests)).toHaveLength(2);
    await supplement('0');
    expect(screen.getByRole('alert').textContent).toContain('1 到 12');
    expect(messagePosts(requests)).toHaveLength(2);
    await supplement('2');
    expect(screen.getByRole('heading', { name: '输入已齐备，尚未执行计算' })).toBeTruthy();
    expect(document.body.textContent).toContain('计费月数：2个月');
    expect(screen.queryByRole('button', { name: '提交补充条件' })).toBeNull();
    expect((screen.getByRole('button', { name: '计算标准规则估算' }) as HTMLButtonElement).disabled).toBe(true);
    expect(firstInput().disabled).toBe(true);
    expect(messagePosts(requests)).toHaveLength(3);
    expect(sourceReads(requests)).toHaveLength(3);
    const bodies = await Promise.all(messagePosts(requests).map(item => item.clone().json()));
    expect(bodies.map(item => item.expected_version)).toEqual([0, 1, 2]);
    expect(bodies.map(item => item.request_id)).toEqual([REQUEST_ID, CONDITION_REQUEST_ID, MONTH_REQUEST_ID]);
    expect(bodies.every(item => item.mode === 'continue' && item.confirm_billing_rule === false)).toBe(true);
    expectNoPrivateOutput();
  });

  it('非法数字本地拒绝；首次保存来源未核对前不显示下一字段，不可连点', async () => {
    const pending = deferred<Response>();
    const requests = setup(undefined, () => pending.promise);
    await start();
    for (const value of ['', '-1', '1.2', '1e2', '10000', '三次']) {
      await supplement(value);
      expect(screen.getByRole('alert').textContent).toContain('整数');
    }
    expect(messagePosts(requests)).toHaveLength(1);
    await supplement('3');
    expect(screen.queryByLabelText('补充计费月数')).toBeNull();
    expect(screen.queryByRole('button', { name: '提交补充条件' })).toBeNull();
    expect(document.body.textContent).not.toContain(RULE);
    expect(messagePosts(requests)).toHaveLength(2);
    await resolveResponse(pending, jsonResponse(documentResponse()));
    expect(screen.getByLabelText('补充计费月数')).toBeTruthy();
  });

  it.each([409, 502])('补充HTTP%s后保留提交快照，读取同版本也不能解锁', async status => {
    const requests = setup(() => jsonResponse({ detail: PRIVATE }, status));
    await start();
    await supplement('3');
    expect(document.body.textContent).toContain('本次补充提交：3次');
    expect(screen.queryByRole('button', { name: '提交补充条件' })).toBeNull();
    expect(document.body.textContent).not.toContain(RULE);
    await click(screen.getByRole('button', { name: '读取当前会话状态' }));
    expect(screen.queryByRole('button', { name: '提交补充条件' })).toBeNull();
    expect(messagePosts(requests)).toHaveLength(2);
    expectNoPrivateOutput();
  });

  it('停止等待补充后迟到响应无效，返回文档列表不自动重发', async () => {
    const pending = deferred<Response>();
    const requests = setup(() => pending.promise);
    await start();
    await supplement('3');
    await click(screen.getByRole('button', { name: '停止等待补充结果' }));
    expect(document.body.textContent).toContain('补充条件结果未确认');
    await resolveResponse(pending, jsonResponse(conditionReply()));
    expect(screen.queryByLabelText('补充计费月数')).toBeNull();
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(screen.getByRole('button', { name: '会话问答' })).toBeTruthy();
    expect(messagePosts(requests)).toHaveLength(2);
  });

  it.each([401, 404])('补充来源%s隐藏旧规则，401退出登录、404暂停', async status => {
    const requests = setup(undefined, () => jsonResponse({ detail: PRIVATE }, status));
    await start();
    await supplement('3');
    expect(document.body.textContent).not.toContain(RULE);
    expect(screen.queryByRole('button', { name: '提交补充条件' })).toBeNull();
    if (status === 401) expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();
    else expect(document.body.textContent).toContain('补充条件结果未确认');
    expect(messagePosts(requests)).toHaveLength(2);
    expectNoPrivateOutput();
  });

  it.each(['logout', 'expiry', 'back'])('补充等待期间%s隔离迟到回执，不恢复条件或自动重发', async action => {
    const pending = deferred<Response>();
    const requests = setup(() => pending.promise);
    await start();
    await supplement('3');
    if (action === 'logout') await click(screen.getByRole('button', { name: '退出登录' }));
    else if (action === 'expiry') await advance(1_800_000);
    else await click(screen.getByRole('button', { name: '返回文档列表' }));
    await resolveResponse(pending, jsonResponse(conditionReply()));
    expect(screen.queryByLabelText('补充计费月数')).toBeNull();
    expect(document.body.textContent).not.toContain(RULE);
    expect(messagePosts(requests)).toHaveLength(2);
    if (action !== 'back') expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();
  });

  it('手动读取发现版本变化时清除可提交条件，不以GET内容重建任务', async () => {
    const requests = installApi(request => {
      if (pathOf(request) === MESSAGES_PATH) return jsonResponse(billingWaitingReply());
      if (pathOf(request) === CONVERSATION_PATH) return jsonResponse({ ...conditionReply(),
        task: { ...conditionReply().task, question: PRIVATE } });
    });
    await start();
    expect(screen.getByLabelText('补充上门次数')).toBeTruthy();
    await click(screen.getByRole('button', { name: '读取当前会话状态' }));
    expect(screen.queryByRole('button', { name: '提交补充条件' })).toBeNull();
    expect(document.body.textContent).toContain('会话记录已变化');
    expect(document.body.textContent).not.toContain(RULE);
    expectNoPrivateOutput();
    expect(messagePosts(requests)).toHaveLength(1);
  });
});

describe('标准计费页内确认与显式执行', () => {
  function installExecution(options: {
    execution?: () => Response | Promise<Response>;
    source?: () => Response | Promise<Response>;
    snapshot?: () => Response;
    logout?: () => Response;
  } = {}) {
    let posts = 0;
    vi.mocked(crypto.randomUUID).mockReturnValueOnce(REQUEST_ID).mockReturnValueOnce(CONDITION_REQUEST_ID)
      .mockReturnValueOnce(MONTH_REQUEST_ID).mockReturnValueOnce(EXECUTION_REQUEST_ID);
    return installApi(request => {
      if (pathOf(request) === MESSAGES_PATH) {
        posts += 1;
        if (posts === 1) return jsonResponse(billingWaitingReply());
        if (posts < 4) return jsonResponse(conditionReply(posts === 3));
        return options.execution?.() ?? jsonResponse(billingExecutionReply());
      }
      if (posts === 4 && pathOf(request) === DOCUMENT_PATH) return options.source?.();
      if (pathOf(request) === LOGOUT) return options.logout?.();
      if (pathOf(request) === CONVERSATION_PATH) return options.snapshot?.() ?? jsonResponse(billingExecutionReply());
    });
  }
  async function readyPage() {
    render(<App />); await openConversation(); await create(); await send();
    await supplement('3'); await supplement('2');
  }
  async function confirmAndExecute() {
    await click(screen.getByRole('checkbox', { name: '确认该规则适用于上述同一计费期间' }));
    await click(screen.getByRole('button', { name: '计算标准规则估算' }));
  }

  it('ready不自动执行，勾选不发送；一次点击后展示三步估算与来源、输入和边界', async () => {
    const requests = installExecution();
    await readyPage();
    const button = screen.getByRole('button', { name: '计算标准规则估算' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    await click(button);
    expect(messagePosts(requests)).toHaveLength(3);
    await click(screen.getByRole('checkbox'));
    expect(button.disabled).toBe(false);
    expect(messagePosts(requests)).toHaveLength(3);
    await click(button);
    const result = screen.getByRole('region', { name: '标准规则估算结果' });
    for (const value of ['240.00', '90.00', '330.00', RULE, '2 个月', '3 次', '程序计算', '不是实际账单', '未产生扣款']) {
      expect(result.textContent).toContain(value);
    }
    expect(result.textContent).toContain('导出说明.md · 文本块 1');
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByRole('button', { name: '计算标准规则估算' })).toBeNull();
    expect(messagePosts(requests)).toHaveLength(4);
    const posted = await messagePosts(requests)[3].clone().json();
    expect(posted).toMatchObject({ request_id: EXECUTION_REQUEST_ID, expected_version: 3,
      mode: 'execute', confirm_billing_rule: true, response_mode: 'knowledge' });
    await click(screen.getByRole('button', { name: '读取当前会话状态' }));
    expect(screen.getByRole('region', { name: '标准规则估算结果' })).toBeTruthy();
    expect(messagePosts(requests)).toHaveLength(4);
    expectNoPrivateOutput();
  });

  it('保存与来源核对期间不显示金额，不允许连点再次执行', async () => {
    const pending = deferred<Response>();
    const requests = installExecution({ source: () => pending.promise });
    await readyPage(); await confirmAndExecute();
    expect(screen.queryByRole('region', { name: '标准规则估算结果' })).toBeNull();
    expect(screen.queryByRole('button', { name: '计算标准规则估算' })).toBeNull();
    expect(document.body.textContent).not.toContain('330.00');
    expect(document.body.textContent).not.toContain(RULE);
    expect(messagePosts(requests)).toHaveLength(4);
    await resolveResponse(pending, jsonResponse(documentResponse()));
    expect(screen.getByRole('region', { name: '标准规则估算结果' })).toBeTruthy();
    expect(sourceReads(requests)).toHaveLength(4);
  });

  it.each([409, 502])('执行HTTP%s后读取completed也不恢复结果或解锁重发', async status => {
    const requests = installExecution({ execution: () => jsonResponse({ detail: PRIVATE }, status) });
    await readyPage(); await confirmAndExecute();
    expect(screen.queryByRole('region', { name: '标准规则估算结果' })).toBeNull();
    await click(screen.getByRole('button', { name: '读取当前会话状态' }));
    expect(screen.queryByRole('region', { name: '标准规则估算结果' })).toBeNull();
    expect(screen.queryByRole('button', { name: '计算标准规则估算' })).toBeNull();
    expect(messagePosts(requests)).toHaveLength(4);
    expectNoPrivateOutput();
  });

  it.each(['cancel', 'back', 'logout', 'expiry'])('执行期间%s隔离迟到200，不自动执行或恢复结果', async action => {
    const pending = deferred<Response>();
    const requests = installExecution({ execution: () => pending.promise });
    await readyPage(); await confirmAndExecute();
    if (action === 'cancel') await click(screen.getByRole('button', { name: '停止等待估算结果' }));
    else if (action === 'back') await click(screen.getByRole('button', { name: '返回文档列表' }));
    else if (action === 'logout') await click(screen.getByRole('button', { name: '退出登录' }));
    else await advance(1_800_000);
    await resolveResponse(pending, jsonResponse(billingExecutionReply()));
    expect(screen.queryByRole('region', { name: '标准规则估算结果' })).toBeNull();
    expect(screen.queryByRole('button', { name: '计算标准规则估算' })).toBeNull();
    expect(messagePosts(requests)).toHaveLength(4);
    if (action === 'cancel') expect(document.body.textContent).toContain('标准规则估算结果未确认');
    expectNoPrivateOutput();
  });

  it.each([401, 404])('执行来源%s失败时隐藏金额和规则', async status => {
    const requests = installExecution({ source: () => jsonResponse({ detail: PRIVATE }, status) });
    await readyPage(); await confirmAndExecute();
    expect(screen.queryByRole('region', { name: '标准规则估算结果' })).toBeNull();
    expect(document.body.textContent).not.toContain(RULE);
    expect(document.body.textContent).not.toContain('330.00');
    if (status === 401) expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();
    else expect(document.body.textContent).toContain('标准规则估算结果未确认');
    expect(messagePosts(requests)).toHaveLength(4);
    expectNoPrivateOutput();
  });

  it('已确认估算后状态GET404会清除结果和规则，不恢复执行按钮', async () => {
    const requests = installExecution({ snapshot: () => jsonResponse({ detail: PRIVATE }, 404) });
    await readyPage(); await confirmAndExecute();
    expect(screen.getByRole('region', { name: '标准规则估算结果' })).toBeTruthy();
    await click(screen.getByRole('button', { name: '读取当前会话状态' }));
    expect(screen.queryByRole('region', { name: '标准规则估算结果' })).toBeNull();
    expect(document.body.textContent).not.toContain(RULE);
    expect(screen.queryByRole('button', { name: '计算标准规则估算' })).toBeNull();
    expect(messagePosts(requests)).toHaveLength(4);
  });

  it('退出失败仍保留被中断估算的未确认状态，迟到回执不能恢复结果', async () => {
    const pending = deferred<Response>();
    const requests = installExecution({ execution: () => pending.promise,
      logout: () => jsonResponse({ detail: PRIVATE }, 502) });
    await readyPage(); await confirmAndExecute();
    await click(screen.getByRole('button', { name: '退出登录' }));
    await resolveResponse(pending, jsonResponse(billingExecutionReply()));
    expect(document.body.textContent).toContain('标准规则估算结果未确认');
    expect(screen.queryByRole('region', { name: '标准规则估算结果' })).toBeNull();
    expect(screen.queryByRole('button', { name: '计算标准规则估算' })).toBeNull();
    expect(messagePosts(requests)).toHaveLength(4);
    expectNoPrivateOutput();
  });
});

describe('网页显式创建会话与首条消息', () => {
  it('第一页零份可检索仍可进入；进入页面不创建、不读取会话、不调用单次问答或检索', async () => {
    const requests = installApi();
    render(<App />);
    await openDocuments();
    expect(document.body.textContent).toContain('0 份可检索');
    const entry = screen.getByRole('button', { name: '会话问答' }) as HTMLButtonElement;
    expect(entry.disabled).toBe(false);
    await click(entry);
    expect(screen.getByRole('heading', { level: 1, name: '会话问答' })).toBeTruthy();
    expect(document.body.textContent).toContain('甲空间');
    expect(document.body.textContent).toContain('甲知识库');
    expect((screen.getByRole('button', { name: '创建会话' }) as HTMLButtonElement).disabled).toBe(false);
    const input = screen.queryByLabelText('首条消息') as HTMLTextAreaElement | null;
    if (input) expect(input.disabled).toBe(true);
    const read = screen.queryByRole('button', { name: '读取当前会话状态' }) as HTMLButtonElement | null;
    if (read) expect(read.disabled).toBe(true);
    expect(creationPosts(requests)).toHaveLength(0);
    expect(messagePosts(requests)).toHaveLength(0);
    expect(snapshotReads(requests)).toHaveLength(0);
    expect(requests.some(request => /\/(answers|search)$/.test(pathOf(request)))).toBe(false);
    expectNoPrivateOutput();
  });

  it('文档仍在读取或读取失败时不能进入会话页', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => pathOf(request) === DOCUMENTS ? pending.promise : undefined);
    render(<App />);
    await openDocuments();
    expect((screen.getByRole('button', { name: '会话问答' }) as HTMLButtonElement).disabled).toBe(true);
    await resolveResponse(pending, jsonResponse({ detail: PRIVATE }, 404));
    const entry = screen.getByRole('button', { name: '会话问答' }) as HTMLButtonElement;
    expect(entry.disabled).toBe(true);
    await click(entry);
    expect(screen.queryByRole('heading', { level: 1, name: '会话问答' })).toBeNull();
    expect(creationPosts(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it('创建与发首问是两个独立动作；创建等待中不能重复提交，201确认后也不会自动发消息', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => pathOf(request) === CONVERSATIONS_PATH ? pending.promise : undefined);
    render(<App />);
    await openConversation();
    await create();
    const running = screen.getByRole('button', { name: '正在创建会话…' }) as HTMLButtonElement;
    expect(running.disabled).toBe(true);
    await click(running);
    expect(creationPosts(requests)).toHaveLength(1);
    expect(messagePosts(requests)).toHaveLength(0);
    await resolveResponse(pending, jsonResponse(createdConversation(), 201));
    expect(firstInput().disabled).toBe(false);
    await fillMessage(QUESTION);
    expect(messagePosts(requests)).toHaveLength(0);
    expect(snapshotReads(requests)).toHaveLength(0);
    const read = screen.queryByRole('button', { name: '读取当前会话状态' }) as HTMLButtonElement | null;
    if (read) expect(read.disabled).toBe(true);
    const [post] = creationPosts(requests);
    expect(post.credentials).toBe('same-origin');
    expect(post.headers.get('X-EAAP-Web')).toBe('1');
    expect(post.headers.has('Authorization')).toBe(false);
    expect((await post.text())).toBe('');
    expectNoPrivateOutput();
  });

  it.each(['network', 'invalid-201'])('创建%s结果未知时不信任会话ID、不自动再次创建也不能发消息', async failure => {
    const requests = installApi(request => {
      if (pathOf(request) !== CONVERSATIONS_PATH) return;
      return failure === 'network' ? Promise.reject(new TypeError(PRIVATE))
        : jsonResponse({ ...createdConversation(), version: 1 }, 201);
    });
    render(<App />);
    await openConversation();
    await create();
    expect(document.body.textContent).toMatch(/会话创建结果未确认/);
    expect(document.body.textContent).toContain('未自动再次创建');
    expect(document.body.textContent).not.toContain(CONVERSATION_ID);
    const button = screen.queryByRole('button', { name: '创建会话' }) as HTMLButtonElement | null;
    if (button) { expect(button.disabled).toBe(true); await click(button); }
    const input = screen.queryByLabelText('首条消息') as HTMLTextAreaElement | null;
    if (input) expect(input.disabled).toBe(true);
    await advance(30_000);
    expect(creationPosts(requests)).toHaveLength(1);
    expect(messagePosts(requests)).toHaveLength(0);
    expect(snapshotReads(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it('创建确认后空白及超长首问本地拒绝，编辑不发送请求', async () => {
    const requests = installApi();
    render(<App />);
    await openConversation();
    await create();
    for (const value of ['', ' \n\t ', '🙂'.repeat(2001)]) {
      await send(value);
      expect(screen.getByRole('alert')).toBeTruthy();
      expect(messagePosts(requests)).toHaveLength(0);
    }
    await fillMessage(QUESTION);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(messagePosts(requests)).toHaveLength(0);
    expect(creationPosts(requests)).toHaveLength(1);
  });

  it('首问固定绑定已创建ID、请求编号、初始版本与提交正文，等待中不能重复发送', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => pathOf(request) === MESSAGES_PATH ? pending.promise : undefined);
    render(<App />);
    await openConversation();
    await create();
    await send(`\u3000 ${QUESTION}\n`);
    const running = screen.getByRole('button', { name: '正在处理首条消息…' }) as HTMLButtonElement;
    expect(running.disabled).toBe(true);
    expect(firstInput().disabled).toBe(true);
    await click(running);
    expect(messagePosts(requests)).toHaveLength(1);
    const [post] = messagePosts(requests);
    expect(await post.clone().json()).toEqual({ request_id: REQUEST_ID, expected_version: 0,
      content: QUESTION, mode: 'continue', confirm_billing_rule: false, response_mode: 'knowledge' });
    expect(post.credentials).toBe('same-origin');
    expect(post.headers.get('X-EAAP-Web')).toBe('1');
    expect(post.headers.get('Content-Type')).toBe('application/json');
    expect(post.headers.has('Authorization')).toBe(false);
    expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
    expect(snapshotReads(requests)).toHaveLength(0);
    expect(requests.some(request => /\/(answers|search)$/.test(pathOf(request)))).toBe(false);
    await resolveResponse(pending, jsonResponse(completedReply()));
    const result = screen.getByRole('region', { name: '本轮会话结果' });
    expect(result.textContent).toContain('本次提交的消息：');
    expect(result.textContent).toContain(QUESTION);
    expect(result.textContent).toContain(ANSWER);
    await expectNoSecondMessage(requests);
    expectNoPrivateOutput();
  });

  it('正常弃答展示首问与原因，不当成技术失败、不读取来源、不开放第二条消息', async () => {
    const requests = installApi(request => pathOf(request) === MESSAGES_PATH ? jsonResponse(abstainedReply()) : undefined);
    render(<App />);
    await openConversation();
    await create();
    await send();
    const result = screen.getByRole('region', { name: '本轮会话结果' });
    expect(result.textContent).toContain(QUESTION);
    expect(result.textContent).toContain(ABSTENTION);
    expect(screen.getByRole('heading', { name: '证据不足，本轮弃答' })).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(sourceReads(requests)).toHaveLength(0);
    await expectNoSecondMessage(requests);
    expectNoPrivateOutput();
  });

  it.each(['failed', 'stopped'] as const)('HTTP200的合法%s运行如实显示，不冒充回答成功或网络未确认', async status => {
    const requests = installApi(request => pathOf(request) === MESSAGES_PATH ? jsonResponse(failedReply(status)) : undefined);
    render(<App />);
    await openConversation();
    await create();
    await send();
    expect(screen.getByRole('heading', { name: status === 'failed' ? '本轮运行失败' : '本轮运行已停止' })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/首条消息结果未确认/);
    expect(document.body.textContent).not.toContain(ANSWER);
    expect(screen.queryByRole('heading', { name: '证据不足，本轮弃答' })).toBeNull();
    expect(sourceReads(requests)).toHaveLength(0);
    await expectNoSecondMessage(requests);
    expectNoPrivateOutput();
  });

  it('全部唯一来源确认前隐藏回答；确认后完整展示两段引文并将HTML字样作为纯文本', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => pathOf(request) === OTHER_DOCUMENT_PATH ? pending.promise : undefined);
    const { container } = render(<App />);
    await openConversation();
    await create();
    await send();
    expect(sourceReads(requests).map(pathOf)).toEqual([DOCUMENT_PATH, OTHER_DOCUMENT_PATH]);
    expectNoResult();
    expect(screen.queryByRole('region', { name: '本轮会话结果' })).toBeNull();
    expect(document.body.textContent).not.toContain(LONG_QUOTE);
    expect(document.body.textContent).not.toContain(OTHER_QUOTE);
    await resolveResponse(pending, jsonResponse(documentResponse(OTHER_DOCUMENT_ID)));
    const result = screen.getByRole('region', { name: '本轮会话结果' });
    expect(result.querySelectorAll('blockquote')).toHaveLength(2);
    for (const text of [ANSWER, LONG_QUOTE, OTHER_QUOTE, '导出说明.md', '保留说明.md']) {
      expect(result.textContent).toContain(text);
    }
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(sourceReads(requests).filter(request => pathOf(request) === DOCUMENT_PATH)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('旧单字段待补任务也先核对全部来源，再展示原规则、字段、单位与范围，再开放补充提交', async () => {
    const pending = deferred<Response>();
    const reply = waitingReply();
    const requests = installApi(request => {
      if (pathOf(request) === MESSAGES_PATH) return jsonResponse(reply);
      if (pathOf(request) === OTHER_DOCUMENT_PATH) return pending.promise;
    });
    render(<App />);
    await openConversation();
    await create();
    await send();
    expectNoResult();
    expect(screen.queryByRole('region', { name: '本轮会话结果' })).toBeNull();
    expect(document.body.textContent).not.toContain(reply.task.clarification.rule_quote);
    expect(screen.queryByRole('heading', { name: '等待补充条件' })).toBeNull();
    expect(sourceReads(requests).map(pathOf)).toEqual([DOCUMENT_PATH, OTHER_DOCUMENT_PATH]);
    await resolveResponse(pending, jsonResponse(documentResponse(OTHER_DOCUMENT_ID)));
    const result = screen.getByRole('region', { name: '补充条件' });
    expect(screen.getByRole('heading', { name: '等待补充条件' })).toBeTruthy();
    for (const value of [reply.task.clarification.rule_quote, '上门次数', '次', '0', '9999']) {
      expect(result.textContent).toContain(value);
    }
    expect(document.body.textContent).not.toContain(LONG_QUOTE);
    expect(document.body.textContent).not.toContain(OTHER_QUOTE);
    expect(document.body.textContent).not.toContain(reply.reply);
    const ruleSources = screen.getByText(/^规则来源：/);
    expect(ruleSources.textContent).toContain('导出说明.md');
    expect(ruleSources.textContent).not.toContain('保留说明.md');
    expect(screen.queryByRole('button', { name: /执行|开始新任务/ })).toBeNull();
    await expectNoSecondMessage(requests);
    expectNoPrivateOutput();
  });

  it('标准计费双字段待补展示同一原规则及两个条件，明确本轮尚未执行费用计算', async () => {
    const requests = installApi(request => pathOf(request) === MESSAGES_PATH ? jsonResponse(billingWaitingReply()) : undefined);
    render(<App />);
    await openConversation();
    await create();
    await send();
    const result = screen.getByRole('region', { name: '补充条件' });
    expect(result.textContent).toContain('上门次数（当前待补）');
    expect(result.textContent).not.toContain('计费月数（当前待补）');
    expect(screen.getByRole('heading', { name: '等待补充条件' })).toBeTruthy();
    for (const value of [RULE, '上门次数', '计费月数', '次', '个月', '9999', '12']) {
      expect(result.textContent).toContain(value);
    }
    expect(screen.queryByRole('button', { name: /执行|开始新任务/ })).toBeNull();
    expect(sourceReads(requests)).toHaveLength(1);
    await expectNoSecondMessage(requests);
    expectNoPrivateOutput();
  });

  it('第二来源不再属于当前知识库时整批隐藏，不展示已确认来源的答案或规则', async () => {
    const requests = installApi(request => pathOf(request) === OTHER_DOCUMENT_PATH
      ? jsonResponse({ ...documentResponse(OTHER_DOCUMENT_ID), knowledge_base_id: OTHER_KNOWLEDGE_BASE_ID }) : undefined);
    render(<App />);
    await openConversation();
    await create();
    await send();
    expect(document.body.textContent).toMatch(/首条消息结果未确认/);
    expectNoResult();
    expect(document.body.textContent).not.toContain(LONG_QUOTE);
    expect(document.body.textContent).not.toContain(OTHER_QUOTE);
    await expectNoSecondMessage(requests);
    expectNoPrivateOutput();
  });

  it.each([0, 2])('消息结果未知后只能显式GET核对版本%i；快照不能充当本轮回复或重新开放首问', async version => {
    const requests = installApi(request => {
      if (pathOf(request) === MESSAGES_PATH) return Promise.reject(new TypeError(PRIVATE));
      if (pathOf(request) === CONVERSATION_PATH) return jsonResponse({ ...createdConversation(), version,
        status: version === 0 ? 'idle' : 'ready', task: version === 0 ? null : { internal: PRIVATE },
        recent_messages: [{ role: 'assistant', content: ANSWER }], billing_result: { internal: PRIVATE } });
    });
    render(<App />);
    await openConversation();
    await create();
    await send();
    expect(document.body.textContent).toMatch(/首条消息结果未确认/);
    expect(firstInput().value).toBe(QUESTION);
    expect(firstInput().disabled).toBe(true);
    expect(snapshotReads(requests)).toHaveLength(0);
    await advance(30_000);
    expect(messagePosts(requests)).toHaveLength(1);
    expect(snapshotReads(requests)).toHaveLength(0);
    await click(screen.getByRole('button', { name: '读取当前会话状态' }));
    expect(snapshotReads(requests)).toHaveLength(1);
    const snapshot = screen.getByRole('region', { name: '当前会话状态' });
    expect(snapshot.textContent).toContain(String(version));
    expectNoResult();
    expect(document.body.textContent).not.toContain(ANSWER);
    expect(document.body.textContent).toMatch(/首条消息结果未确认/);
    await expectNoSecondMessage(requests);
    expect(creationPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('已有确认回答后读取状态404会清除受保护答案与引文，只保留安全错误且不重发首问', async () => {
    const requests = installApi(request => pathOf(request) === CONVERSATION_PATH
      ? jsonResponse({ detail: PRIVATE }, 404) : undefined);
    render(<App />);
    await openConversation();
    await create();
    await send();
    const confirmed = screen.getByRole('region', { name: '本轮会话结果' });
    expect(confirmed.textContent).toContain(ANSWER);
    expect(confirmed.querySelectorAll('blockquote')).toHaveLength(2);
    await click(screen.getByRole('button', { name: '读取当前会话状态' }));
    expectNoResult();
    expect(screen.queryByRole('region', { name: '本轮会话结果' })).toBeNull();
    expect(document.querySelectorAll('blockquote')).toHaveLength(0);
    expect(document.body.textContent).not.toContain(RULE);
    const state = screen.getByRole('region', { name: '当前会话状态' });
    expect(state.textContent).toContain('该知识库或会话不存在，或你已无权访问');
    expect(screen.getByRole('alert').textContent).toContain('无权访问');
    expect(snapshotReads(requests)).toHaveLength(1);
    await expectNoSecondMessage(requests);
    expect(creationPosts(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it.each(['cancel', 'back'])('创建期间%s后迟到201不能恢复已失效会话或自动发送消息', async action => {
    const pending = deferred<Response>();
    const requests = installApi(request => pathOf(request) === CONVERSATIONS_PATH ? pending.promise : undefined);
    render(<App />);
    await openConversation();
    await create();
    const [post] = creationPosts(requests);
    await click(screen.getByRole('button', { name: action === 'cancel' ? '停止等待创建结果' : '返回文档列表' }));
    expect(post.signal.aborted).toBe(true);
    if (action === 'cancel') expect(document.body.textContent).toMatch(/会话创建结果未确认/);
    else expect(screen.getByRole('heading', { level: 1, name: '文档' })).toBeTruthy();
    await resolveResponse(pending, jsonResponse(createdConversation(), 201));
    expect(document.body.textContent).not.toContain(CONVERSATION_ID);
    expect(messagePosts(requests)).toHaveLength(0);
    expect(snapshotReads(requests)).toHaveLength(0);
    expect(creationPosts(requests)).toHaveLength(1);
    if (action === 'back') expect(screen.queryByRole('heading', { level: 1, name: '会话问答' })).toBeNull();
    expectNoPrivateOutput();
  });

  it('停止等待消息保留已知ID和首问请求，迟到回答不展示也不自动重发', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => pathOf(request) === MESSAGES_PATH ? pending.promise : undefined);
    render(<App />);
    await openConversation();
    await create();
    await send();
    const [post] = messagePosts(requests);
    await click(screen.getByRole('button', { name: '停止等待消息结果' }));
    expect(post.signal.aborted).toBe(true);
    expect(document.body.textContent).toMatch(/首条消息结果未确认/);
    expect(firstInput().value).toBe(QUESTION);
    expect(firstInput().disabled).toBe(true);
    await resolveResponse(pending, jsonResponse(completedReply()));
    expectNoResult();
    expect(sourceReads(requests)).toHaveLength(0);
    await expectNoSecondMessage(requests);
    expect(snapshotReads(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it('停止读取状态只取消本次GET，迟到快照不能改变已呈现的状态或伪造本轮答案', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === MESSAGES_PATH) return Promise.reject(new TypeError(PRIVATE));
      if (pathOf(request) === CONVERSATION_PATH) return pending.promise;
    });
    render(<App />);
    await openConversation();
    await create();
    await send();
    await click(screen.getByRole('button', { name: '读取当前会话状态' }));
    const [get] = snapshotReads(requests);
    await click(screen.getByRole('button', { name: '停止读取状态' }));
    expect(get.signal.aborted).toBe(true);
    const previous = screen.getByRole('region', { name: '当前会话状态' }).textContent;
    await resolveResponse(pending, jsonResponse({ ...createdConversation(), version: 3, status: 'ready',
      task: { internal: PRIVATE }, recent_messages: [{ role: 'assistant', content: ANSWER }] }));
    expect(screen.getByRole('region', { name: '当前会话状态' }).textContent).toBe(previous);
    expectNoResult();
    await expectNoSecondMessage(requests);
    expect(snapshotReads(requests)).toHaveLength(1);
    expectNoPrivateOutput();
  });

  it('消息期间返回并切换知识库清理会话ID和首问；旧响应不能越过新页面边界', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => pathOf(request) === MESSAGES_PATH ? pending.promise : undefined);
    render(<App />);
    await openConversation();
    await create();
    await send();
    const [post] = messagePosts(requests);
    await click(screen.getByRole('button', { name: '返回文档列表' }));
    expect(post.signal.aborted).toBe(true);
    await click(screen.getByRole('button', { name: '返回知识库' }));
    await click(screen.getByRole('button', { name: '查看乙知识库的文档' }));
    await click(screen.getByRole('button', { name: '会话问答' }));
    expect(document.body.textContent).toContain('乙知识库');
    await resolveResponse(pending, jsonResponse(completedReply()));
    expectNoResult();
    for (const value of [CONVERSATION_ID, REQUEST_ID, QUESTION]) expect(document.body.textContent).not.toContain(value);
    const createButton = screen.getByRole('button', { name: '创建会话' }) as HTMLButtonElement;
    expect(createButton.disabled).toBe(false);
    expect(messagePosts(requests)).toHaveLength(1);
    expect(creationPosts(requests)).toHaveLength(1);
    expect(sourceReads(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it('消息期间退出失败保留未确认结果与已知会话，隔离旧回答并只允许只读核对', async () => {
    const pending = deferred<Response>();
    const exiting = deferred<Response>();
    const requests = installApi(request => {
      if (pathOf(request) === MESSAGES_PATH) return pending.promise;
      if (pathOf(request) === LOGOUT) return exiting.promise;
    });
    render(<App />);
    await openConversation();
    await create();
    await send();
    const [post] = messagePosts(requests);
    await click(screen.getByRole('button', { name: '退出登录' }));
    expect(post.signal.aborted).toBe(true);
    await resolveResponse(exiting, jsonResponse({ detail: PRIVATE }, 500));
    expect(document.body.textContent).toContain('退出尚未确认');
    expect(document.body.textContent).toMatch(/首条消息结果未确认/);
    expect(firstInput().value).toBe(QUESTION);
    expect(firstInput().disabled).toBe(true);
    await resolveResponse(pending, jsonResponse(completedReply()));
    expectNoResult();
    await expectNoSecondMessage(requests);
    await click(screen.getByRole('button', { name: '读取当前会话状态' }));
    expect(snapshotReads(requests)).toHaveLength(1);
    expectNoResult();
    expectNoPrivateOutput();
  });

  it('固定30分钟登录到期先于消息最长等待，清理会话及首问并隔离迟到成功响应', async () => {
    const pending = deferred<Response>();
    const requests = installApi(request => pathOf(request) === MESSAGES_PATH ? pending.promise : undefined);
    render(<App />);
    await openConversation();
    await create();
    await send();
    const [post] = messagePosts(requests);
    await advance(1_800_001);
    expect(post.signal.aborted).toBe(true);
    expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
    expect(document.body.textContent).toContain('登录已到期');
    await resolveResponse(pending, jsonResponse(completedReply()));
    for (const value of [CONVERSATION_ID, REQUEST_ID, QUESTION, '甲空间', '甲知识库']) {
      expect(document.body.textContent).not.toContain(value);
    }
    expectNoResult();
    expect(messagePosts(requests)).toHaveLength(1);
    expect(sourceReads(requests)).toHaveLength(0);
    expect(snapshotReads(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });

  it('创建、消息、来源和状态读取的401均清理身份、当前范围及会话内容', async () => {
    for (const endpoint of [CONVERSATIONS_PATH, MESSAGES_PATH, OTHER_DOCUMENT_PATH, CONVERSATION_PATH]) {
      const requests = installApi(request => {
        if (pathOf(request) === endpoint) return jsonResponse({ detail: PRIVATE }, 401);
        if (endpoint === CONVERSATION_PATH && pathOf(request) === MESSAGES_PATH) {
          return Promise.reject(new TypeError(PRIVATE));
        }
      });
      const view = render(<App />);
      await openConversation();
      await create();
      if (endpoint !== CONVERSATIONS_PATH) await send();
      if (endpoint === CONVERSATION_PATH) await click(screen.getByRole('button', { name: '读取当前会话状态' }));
      expect(screen.getByRole('heading', { name: '登录工作空间' })).toBeTruthy();
      for (const value of [CONVERSATION_ID, REQUEST_ID, QUESTION, '甲空间', '甲知识库']) {
        expect(document.body.textContent).not.toContain(value);
      }
      expectNoResult();
      expectNoPrivateOutput();
      expect(creationPosts(requests)).toHaveLength(1);
      expect(messagePosts(requests)).toHaveLength(endpoint === CONVERSATIONS_PATH ? 0 : 1);
      view.unmount();
    }
  });

  it('重新挂载仅恢复登录，业务会话ID与首问不持久化、不自动读取或重发', async () => {
    const requests = installApi();
    const first = render(<App />);
    await openConversation();
    await create();
    await send();
    expect(screen.getByRole('region', { name: '本轮会话结果' })).toBeTruthy();
    first.unmount();
    render(<App />);
    await advance();
    expect(screen.getByRole('heading', { level: 1, name: '你的工作空间' })).toBeTruthy();
    for (const value of [CONVERSATION_ID, REQUEST_ID, QUESTION]) expect(document.body.textContent).not.toContain(value);
    expectNoResult();
    await click(screen.getByRole('button', { name: '进入甲空间' }));
    await click(screen.getByRole('button', { name: '查看甲知识库的文档' }));
    await click(screen.getByRole('button', { name: '会话问答' }));
    expect((screen.getByRole('button', { name: '创建会话' }) as HTMLButtonElement).disabled).toBe(false);
    expect(creationPosts(requests)).toHaveLength(1);
    expect(messagePosts(requests)).toHaveLength(1);
    expect(snapshotReads(requests)).toHaveLength(0);
    expectNoPrivateOutput();
  });
});
