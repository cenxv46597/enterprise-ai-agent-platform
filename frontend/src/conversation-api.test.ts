import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './api';
import {
  CONVERSATION_CREATE_UNCONFIRMED_MESSAGE, CONVERSATION_MESSAGE_UNCONFIRMED_MESSAGE,
  ConversationUnconfirmedError, createConversation, getConversationSnapshot, sendFirstConversationMessage,
  type FirstMessageRequest,
} from './conversation-api';
import {
  ABSTENTION, ANSWER, CHUNK_ID, CONVERSATION_ID, CONVERSATION_PATH, CONVERSATIONS_PATH,
  DOCUMENT_ID, DOCUMENT_PATH, KNOWLEDGE_BASE_ID, LONG_QUOTE, MESSAGES_PATH,
  OTHER_DOCUMENT_ID, OTHER_DOCUMENT_PATH, PRIVATE, QUESTION, REQUEST_ID, RULE, WORKSPACE_ID,
  abstainedReply, billingWaitingReply, completedReply, createdConversation, documentResponse,
  failedReply, jsonResponse, waitingReply,
} from './conversation-test-fixtures';

const fetchMock = vi.fn<typeof fetch>();

function send(content = QUESTION, signal = new AbortController().signal) {
  return sendFirstConversationMessage(WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID,
    { requestId: REQUEST_ID, expectedVersion: 0, content }, signal);
}

function snapshot(signal = new AbortController().signal) {
  return getConversationSnapshot(WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID, signal);
}

function create(signal = new AbortController().signal) {
  return createConversation(WORKSPACE_ID, KNOWLEDGE_BASE_ID, signal);
}

async function rejection(action: Promise<unknown>): Promise<ApiError> {
  const error: unknown = await action.then(() => { throw new Error('Expected API rejection'); }, (failure: unknown) => failure);
  expect(error).toBeInstanceOf(ApiError);
  expect(String(error)).not.toContain(PRIVATE);
  return error as ApiError;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(complete => { resolve = complete; });
  return { promise, resolve };
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('首条会话消息的独立HTTP契约', () => {
  it('创建只发送一次无正文POST，201仅返回初始元数据，不自动发送消息或读取状态', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(createdConversation(), 201));
    expect(await create()).toEqual({ id: CONVERSATION_ID, version: 0, status: 'idle' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(CONVERSATIONS_PATH);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: 'POST', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
    });
    expect(fetchMock.mock.calls[0][1]?.body).toBeUndefined();
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get('X-EAAP-Web')).toBe('1');
    expect(headers.has('Authorization')).toBe(false);
    expect(headers.has('Cookie')).toBe(false);
    expect(headers.has('Content-Type')).toBe(false);
  });

  it('拒绝非空或非初始创建响应，不把不明创建结果当作可继续的新会话', async () => {
    const base = createdConversation();
    const invalid: unknown[] = [null, [], { ...base, conversation_id: `${CONVERSATION_ID}\n` },
      { ...base, version: 1 }, { ...base, status: 'waiting_for_user', task: {} },
      { ...base, task: {} }, { ...base, recent_messages: [{ role: 'assistant', content: PRIVATE }] },
      { ...base, recent_messages: null }, { ...base, billing_result: {} },
      { conversation_id: CONVERSATION_ID, version: 0, status: 'idle', task: null, recent_messages: [] }];
    for (const payload of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload, 201));
      expect(await rejection(create())).toMatchObject({
        name: 'ConversationUnconfirmedError', stage: 'create', message: CONVERSATION_CREATE_UNCONFIRMED_MESSAGE,
      });
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('创建拒绝与未知响应均使用安全信息，网络或JSON故障不再次创建', async () => {
    for (const status of [401, 403, 404, 409, 422, 200, 202, 429, 500]) {
      const response = jsonResponse({ detail: PRIVATE }, status);
      const read = vi.spyOn(response, 'json');
      fetchMock.mockResolvedValueOnce(response);
      const error = await rejection(create());
      expect(error.status).toBe(status);
      expect(error instanceof ConversationUnconfirmedError).toBe(![401, 403, 404, 409, 422].includes(status));
      expect(read).not.toHaveBeenCalled();
    }
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE, { status: 201 }));
    expect(await rejection(create())).toBeInstanceOf(ConversationUnconfirmedError);
    fetchMock.mockRejectedValueOnce(new TypeError(PRIVATE));
    expect(await rejection(create())).toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(11);
  });

  it('创建使用十秒等待，迟到201保持未确认且不会链式发送首条消息', async () => {
    vi.useFakeTimers();
    const pending = deferred<Response>();
    fetchMock.mockReturnValueOnce(pending.promise);
    const action = rejection(create());
    await vi.advanceTimersByTimeAsync(9_999);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    pending.resolve(jsonResponse(createdConversation(), 201));
    expect(await action).toMatchObject({ stage: 'create', message: CONVERSATION_CREATE_UNCONFIRMED_MESSAGE });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('首条消息绑定调用方UUID与版本0，固定knowledge/continue/false，不创建或执行其他任务', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(failedReply()));
    await send(`\u3000${QUESTION}\n`);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(MESSAGES_PATH);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: 'POST', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
      body: JSON.stringify({ request_id: REQUEST_ID, expected_version: 0, content: QUESTION,
        mode: 'continue', confirm_billing_rule: false, response_mode: 'knowledge' }),
    });
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get('X-EAAP-Web')).toBe('1');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.has('Authorization')).toBe(false);
    expect(headers.has('Cookie')).toBe(false);
  });

  it('不合法URI标识、问题与非初始版本均在本地拒绝，不产生请求', async () => {
    for (const [workspace, knowledgeBase, conversation, requestId] of [
      [`${WORKSPACE_ID}\n`, KNOWLEDGE_BASE_ID, CONVERSATION_ID, REQUEST_ID],
      [WORKSPACE_ID, '../knowledge-bases', CONVERSATION_ID, REQUEST_ID],
      [WORKSPACE_ID, KNOWLEDGE_BASE_ID, `${CONVERSATION_ID}/messages`, REQUEST_ID],
      [WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID, `${REQUEST_ID}\n`],
    ]) {
      expect(await rejection(sendFirstConversationMessage(workspace, knowledgeBase, conversation,
        { requestId, expectedVersion: 0, content: QUESTION }, new AbortController().signal)))
        .not.toBeInstanceOf(ConversationUnconfirmedError);
    }
    const invalid: unknown[] = ['', ' \n\t ', '😀'.repeat(2001), null, true];
    for (const content of invalid) {
      expect(await rejection(sendFirstConversationMessage(WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID,
        { requestId: REQUEST_ID, expectedVersion: 0, content } as FirstMessageRequest, new AbortController().signal)))
        .not.toBeInstanceOf(ConversationUnconfirmedError);
    }
    expect(await rejection(sendFirstConversationMessage(WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID,
      { requestId: REQUEST_ID, expectedVersion: 1 as 0, content: QUESTION }, new AbortController().signal)))
      .not.toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('按Unicode码点接纳2000字符，正常弃答不读取来源也不自动继续', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(abstainedReply()));
    const question = '😀'.repeat(2000);
    const result = await send(` \n${question}\t`);
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string).content).toBe(question);
    expect(result).toMatchObject({ kind: 'answered', pending: null,
      answer: { answer: ABSTENTION, abstained: true, citations: [] } });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(['failed', 'stopped'] as const)('HTTP200的%s是已确认运行结果，只有安全状态而没有伪造答案', async status => {
    fetchMock.mockResolvedValueOnce(jsonResponse(failedReply(status)));
    expect(await send()).toEqual({ conversationId: CONVERSATION_ID, requestId: REQUEST_ID,
      version: 1, kind: status, answer: null, pending: null, calculationCount: 0 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('完整答案引用绑定运行证据，去重后全部唯一文档核对完成才返回答案与完整引文', async () => {
    const payload = completedReply();
    const secondQuote = '每次上门服务费30元';
    payload.run.result.citations.splice(1, 0, { ...payload.run.result.citations[0],
      quote: secondQuote, start_char: 100, end_char: 100 + Array.from(secondQuote).length });
    const pending = deferred<Response>();
    fetchMock.mockResolvedValueOnce(jsonResponse(payload))
      .mockResolvedValueOnce(jsonResponse(documentResponse())).mockReturnValueOnce(pending.promise);
    let finished = false;
    const action = send().then(value => { finished = true; return value; });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(finished).toBe(false);
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([MESSAGES_PATH, DOCUMENT_PATH, OTHER_DOCUMENT_PATH]);
    pending.resolve(jsonResponse(documentResponse(OTHER_DOCUMENT_ID)));
    const result = await action;
    expect(result).toMatchObject({ kind: 'answered', answer: { answer: ANSWER, abstained: false }, pending: null });
    expect(result.answer?.citations.map(item => item.quote)).toEqual([LONG_QUOTE, secondQuote,
      payload.run.result.citations[2].quote]);
    expect(Object.keys(result).sort()).toEqual([
      'answer', 'calculationCount', 'conversationId', 'kind', 'pending', 'requestId', 'version',
    ]);
    for (const [, options] of fetchMock.mock.calls) {
      expect(options?.credentials).toBe('same-origin');
      expect(new Headers(options?.headers).get('X-EAAP-Web')).toBe('1');
      expect(new Headers(options?.headers).has('Authorization')).toBe(false);
    }
  });

  it('运行证据不含对应块或引用身份、编号及范围不匹配时，在来源GET前整批拒绝', async () => {
    const base = completedReply();
    const original = base.run.result.citations[0];
    const badCitations = [
      { ...original, chunk_id: '44444444-4444-4444-4444-444444444444' },
      { ...original, document_id: OTHER_DOCUMENT_ID }, { ...original, document_name: '另一名字.md' },
      { ...original, chunk_index: 99 },
      { ...original, start_char: 99, end_char: 99 + Array.from(original.quote).length },
    ];
    for (const citation of badCitations) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...base, run: { ...base.run,
        result: { ...base.run.result, citations: [citation] } } }));
      expect(await rejection(send())).toBeInstanceOf(ConversationUnconfirmedError);
    }
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...base, run: { ...base.run, evidence: [] } }));
    expect(await rejection(send())).toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(badCitations.length + 1);
  });

  it('拒绝错会话、错请求、错版本及首轮不可达的ready/completed或相互矛盾的任务状态', async () => {
    const base = completedReply();
    const waiting = waitingReply();
    const invalid: unknown[] = [
      { ...base, conversation_id: REQUEST_ID }, { ...base, request_id: CONVERSATION_ID },
      { ...base, version: 0 }, { ...base, version: 2 }, { ...base, status: 'ready', task: {} },
      { ...base, status: 'completed', task: {} }, { ...base, task: {} },
      { ...base, status: 'waiting_for_user' }, { ...base, reply: PRIVATE },
      { ...base, run: { ...base.run, run_id: 'invalid' } },
      { ...base, run: { ...base.run, status: 'ready' } }, { ...base, billing_result: {} },
      { ...base, execution_deferred: 'explicit_execution_required' },
      { ...waiting, task: null }, { ...waiting, status: 'idle' },
      { ...waiting, run: { ...waiting.run, result: base.run.result } },
      { ...failedReply(), run: { ...failedReply().run, result: base.run.result } },
    ];
    for (const payload of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      expect(await rejection(send())).toMatchObject({ name: 'ConversationUnconfirmedError', stage: 'message' });
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('waiting单字段使用真实task与run证据契约，全部来源完成后返回受控追问而不自动补值', async () => {
    const pending = deferred<Response>();
    fetchMock.mockResolvedValueOnce(jsonResponse(waitingReply()))
      .mockResolvedValueOnce(jsonResponse(documentResponse())).mockReturnValueOnce(pending.promise);
    let finished = false;
    const action = send().then(value => { finished = true; return value; });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(finished).toBe(false);
    pending.resolve(jsonResponse(documentResponse(OTHER_DOCUMENT_ID)));
    const result = await action;
    expect(result).toMatchObject({ kind: 'waiting', answer: null, pending: { kind: 'count', question: QUESTION,
      current: { fieldName: 'visit_count', label: '上门次数', unit: '次', minValue: 0, maxValue: 9999,
        ruleQuote: '每次上门服务费30元', evidenceIds: [1] } } });
    expect(result.pending?.requirements).toHaveLength(1);
    expect(result.pending?.sources).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('waiting计费双字段保留业务角色与明确范围，接纳后端Unicode空白和等值十进制价格', async () => {
    for (const alternate of [false, true]) {
      const payload = billingWaitingReply();
      if (alternate) {
        const rule = '月费\u008580.50元；每次上门服务费25.25元.';
        payload.task.billing_plan.rule_quote = rule;
        for (const item of [payload.task.clarification, ...payload.task.clarifications]) item.rule_quote = rule;
        payload.task.evidence[0].end_char = 100 + Array.from(rule).length;
        payload.run.evidence[0].end_char = 100 + Array.from(rule).length;
        for (const [rate, quote, value] of [
          [payload.task.billing_plan.monthly_fee, '月费\u008580.50元', '80.5'],
          [payload.task.billing_plan.visit_fee, '每次上门服务费25.25元', '25.2500'],
        ] as const) {
          rate.quote = quote; rate.value = value;
          rate.start_char = 100 + rule.indexOf(quote); rate.end_char = rate.start_char + Array.from(quote).length;
        }
      }
      fetchMock.mockResolvedValueOnce(jsonResponse(payload)).mockResolvedValueOnce(jsonResponse(documentResponse()));
      const result = await send();
      expect(result).toMatchObject({ kind: 'waiting', answer: null, pending: { kind: 'billing', question: QUESTION } });
      expect(result.pending?.requirements.map(item => [item.fieldName, item.unit, item.minValue, item.maxValue]))
        .toEqual([['visit_count', '次', 0, 9999], ['billing_months', '个月', 1, 12]]);
      expect(result.pending?.current.fieldName).toBe('visit_count');
    }
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('追问的原问题、证据映射、字段规则与首轮空输入不匹配时拒绝整体结果', async () => {
    const base = waitingReply();
    const task = base.task;
    const invalidTasks = [
      { ...task, question: '另一个原问题' }, { ...task, kind: 'count' },
      { ...task, evidence: [task.evidence[1], task.evidence[0]] },
      { ...task, evidence: [{ ...task.evidence[0], end_char: task.evidence[0].end_char + 1 }, task.evidence[1]] },
      { ...task, confirmed_inputs: [{ field_name: 'visit_count', value: 1, unit: '次', request_id: REQUEST_ID }] },
      { ...task, clarification: { ...task.clarification, evidence_ids: [3] } },
      { ...task, clarification: { ...task.clarification, evidence_ids: [1, 1] } },
      { ...task, clarification: { ...task.clarification, unit: '美元' } },
      { ...task, clarification: { ...task.clarification, min_value: 10, max_value: 1 } },
      { ...task, clarification: { ...task.clarification, rule_quote: '😀'.repeat(401) } },
      { ...task, clarifications: [task.clarification] },
    ];
    for (const invalidTask of invalidTasks) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...base, task: invalidTask }));
      expect(await rejection(send())).toBeInstanceOf(ConversationUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalidTasks.length);
  });

  it('计费双字段拒绝角色交换、非法月份、不同当前字段与不匹配价格或来源范围', async () => {
    const base = billingWaitingReply();
    const task = base.task;
    const plan = task.billing_plan;
    const invalidTasks = [
      { ...task, clarifications: [...task.clarifications].reverse() },
      { ...task, clarifications: [task.clarifications[0], { ...task.clarifications[1], max_value: 13 }] },
      { ...task, clarification: task.clarifications[1] },
      { ...task, billing_plan: { ...plan, workflow: 'arbitrary_formula' } },
      { ...task, billing_plan: { ...plan, monthly_fee: { ...plan.monthly_fee, value: '121' } } },
      { ...task, billing_plan: { ...plan, visit_fee: { ...plan.visit_fee, unit: '元/个月' } } },
      { ...task, billing_plan: { ...plan, monthly_fee: { ...plan.monthly_fee, document_id: OTHER_DOCUMENT_ID } } },
      { ...task, billing_plan: { ...plan, monthly_fee: { ...plan.monthly_fee, end_char: plan.monthly_fee.end_char + 1 } } },
      { ...task, billing_plan: { ...plan, rule_quote: `${RULE}另有优惠。` } },
    ];
    for (const invalidTask of invalidTasks) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...base, task: invalidTask }));
      expect(await rejection(send())).toBeInstanceOf(ConversationUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalidTasks.length);
  });

  it('任一来源401穿透为会话失效，不能转成普通未确认或返回部分答案', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(completedReply()))
      .mockResolvedValueOnce(jsonResponse(documentResponse()))
      .mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, 401));
    const error = await rejection(send());
    expect(error).not.toBeInstanceOf(ConversationUnconfirmedError);
    expect(error).toMatchObject({ status: 401, message: '登录已失效，请重新登录。' });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('任一来源缺失、错范围、错名或未ready均让答案或追问整体未确认', async () => {
    const invalidSources = [
      jsonResponse({ detail: PRIVATE }, 404),
      jsonResponse({ ...documentResponse(OTHER_DOCUMENT_ID), knowledge_base_id: DOCUMENT_ID }),
      jsonResponse({ ...documentResponse(OTHER_DOCUMENT_ID), original_filename: '错名.md' }),
      jsonResponse({ ...documentResponse(OTHER_DOCUMENT_ID), status: 'parsed' }),
    ];
    for (const [index, response] of invalidSources.entries()) {
      fetchMock.mockResolvedValueOnce(jsonResponse(index % 2 === 0 ? completedReply() : waitingReply()))
        .mockResolvedValueOnce(jsonResponse(documentResponse())).mockResolvedValueOnce(response);
      expect(await rejection(send())).toMatchObject({
        name: 'ConversationUnconfirmedError', message: CONVERSATION_MESSAGE_UNCONFIRMED_MESSAGE,
      });
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalidSources.length * 3);
  });

  it('拒绝证据UUID重复、编号不连续、同文档名称矛盾和越界范围，不读取来源', async () => {
    const base = completedReply();
    const first = base.run.evidence[0];
    const second = base.run.evidence[1];
    const invalidEvidence = [
      [first, { ...second, chunk_id: CHUNK_ID.toUpperCase() }],
      [{ ...first, evidence_id: 2 }, second],
      [first, { ...second, document_id: DOCUMENT_ID, document_name: '矛盾名字.md' }],
      [{ ...first, end_char: first.start_char }, second],
      [{ ...first, start_char: true }, second],
      [{ ...first, end_char: first.start_char + 12001 }],
      [{ ...first, chunk_id: `${CHUNK_ID}\n` }, second],
    ];
    for (const evidence of invalidEvidence) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...base, run: { ...base.run, evidence } }));
      expect(await rejection(send())).toBeInstanceOf(ConversationUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalidEvidence.length);
  });

  it('计算只返回受控数量，不透传内部步骤、理由或原始计算结果，并拒绝非法编号', async () => {
    const base = failedReply();
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...base, run: { ...base.run,
      steps: [{ reason: PRIVATE }], calculations: [{ calculation_id: 1, value: PRIVATE }] } }));
    const result = await send();
    expect(result.calculationCount).toBe(1);
    expect(JSON.stringify(result)).not.toContain(PRIVATE);
    for (const calculations of [null, [{ calculation_id: 2 }], [{ calculation_id: true }],
      Array.from({ length: 8 }, (_, index) => ({ calculation_id: index + 1 }))]) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...base, run: { ...base.run, calculations } }));
      expect(await rejection(send())).toBeInstanceOf(ConversationUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it('GET只接纳当前元数据，idle/waiting/ready/completed都不把recent答案当本次请求回执', async () => {
    for (const status of ['idle', 'waiting_for_user', 'ready', 'completed']) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ ...createdConversation(), version: 7, status,
        task: status === 'idle' ? null : waitingReply().task,
        recent_messages: [{ role: 'assistant', content: PRIVATE }], billing_result: { total: PRIVATE } }));
      expect(await snapshot()).toEqual({ id: CONVERSATION_ID, version: 7, status });
    }
    expect(fetchMock).toHaveBeenCalledTimes(4);
    for (const [path, options] of fetchMock.mock.calls) {
      expect(path).toBe(CONVERSATION_PATH);
      expect(options?.method).toBe('GET');
      expect(options?.body).toBeUndefined();
    }
  });

  it('GET拒绝错会话、未知状态、非安全版本与任务关系矛盾，version0仅允许idle', async () => {
    const base = createdConversation();
    const invalid: unknown[] = [
      { ...base, conversation_id: REQUEST_ID }, { ...base, status: 'failed' },
      { ...base, version: -1 }, { ...base, version: 201 }, { ...base, version: true },
      { ...base, version: 1.5 }, { ...base, task: {} },
      { ...base, version: 1, status: 'ready', task: null },
      ...['waiting_for_user', 'ready', 'completed'].map(status => ({ ...base, status, task: {} })),
    ];
    for (const payload of invalid) {
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      expect(await rejection(snapshot())).toBeInstanceOf(ConversationUnconfirmedError);
    }
    expect(fetchMock).toHaveBeenCalledTimes(invalid.length);
  });

  it('首条消息已知HTTP拒绝安全映射且不读取detail或重发，不把409解释为未计费', async () => {
    for (const status of [401, 403, 404, 409, 422]) {
      const response = jsonResponse({ detail: PRIVATE }, status);
      const read = vi.spyOn(response, 'json');
      fetchMock.mockResolvedValueOnce(response);
      const error = await rejection(send());
      expect(error).not.toBeInstanceOf(ConversationUnconfirmedError);
      expect(error.status).toBe(status);
      if (status === 409 || status === 422) expect(error.message).toContain('不能确认是否已产生费用');
      expect(read).not.toHaveBeenCalled();
    }
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it('未知HTTP状态、坏JSON与断线保持首条消息未确认，不自动GET状态或重试', async () => {
    for (const status of [201, 202, 204, 429, 500, 502, 503, 504]) {
      fetchMock.mockResolvedValueOnce(status === 204 ? new Response(null, { status }) : jsonResponse({ detail: PRIVATE }, status));
      expect(await rejection(send())).toMatchObject({ name: 'ConversationUnconfirmedError', stage: 'message', status });
    }
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE));
    expect(await rejection(send())).toBeInstanceOf(ConversationUnconfirmedError);
    fetchMock.mockRejectedValueOnce(new TypeError(PRIVATE));
    expect(await rejection(send())).toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(10);
  });

  it('已取消时创建、首条消息及状态GET均不发送请求', async () => {
    const parent = new AbortController();
    parent.abort();
    expect(await rejection(create(parent.signal))).toMatchObject({ stage: 'create' });
    expect(await rejection(send(QUESTION, parent.signal))).toMatchObject({ stage: 'message' });
    expect(await rejection(snapshot(parent.signal))).toMatchObject({ stage: 'message' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('首条消息十秒仍等待，2430000ms才中断，迟到成功不读取来源', async () => {
    vi.useFakeTimers();
    const pending = deferred<Response>();
    fetchMock.mockReturnValueOnce(pending.promise);
    const action = rejection(send());
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(2_419_999);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    pending.resolve(jsonResponse(completedReply()));
    expect(await action).toMatchObject({ stage: 'message', message: CONVERSATION_MESSAGE_UNCONFIRMED_MESSAGE });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('解析响应正文期间取消后，迟到JSON不能确认答案或触发来源核对', async () => {
    const pending = deferred<unknown>();
    const response = jsonResponse(completedReply());
    const read = vi.spyOn(response, 'json').mockReturnValueOnce(pending.promise);
    fetchMock.mockResolvedValueOnce(response);
    const parent = new AbortController();
    const action = rejection(send(QUESTION, parent.signal));
    await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    parent.abort();
    pending.resolve(completedReply());
    expect(await action).toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('来源核对期间取消后，迟到合法来源也不能返回答案或继续核对第二文档', async () => {
    const pending = deferred<Response>();
    fetchMock.mockResolvedValueOnce(jsonResponse(completedReply())).mockReturnValueOnce(pending.promise);
    const parent = new AbortController();
    const action = rejection(send(QUESTION, parent.signal));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    parent.abort();
    pending.resolve(jsonResponse(documentResponse()));
    expect(await action).toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('长消息POST完成后来源GET有独立十秒预算，状态GET也只等待十秒并清理监听', async () => {
    vi.useFakeTimers();
    const pending = deferred<Response>();
    fetchMock.mockReturnValueOnce(pending.promise).mockImplementationOnce((_path, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new TypeError(PRIVATE)), { once: true });
    }));
    const parent = new AbortController();
    const removeListener = vi.spyOn(parent.signal, 'removeEventListener');
    const action = rejection(send(QUESTION, parent.signal));
    await vi.advanceTimersByTimeAsync(2_429_999);
    pending.resolve(jsonResponse(completedReply()));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(9_999);
    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await action).toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(true);
    fetchMock.mockImplementationOnce((_path, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new TypeError(PRIVATE)), { once: true });
    }));
    const current = rejection(snapshot(parent.signal));
    await vi.advanceTimersByTimeAsync(9_999);
    expect(fetchMock.mock.calls[2][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await current).toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock.mock.calls[2][0]).toBe(CONVERSATION_PATH);
    expect(fetchMock.mock.calls[2][1]?.signal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(vi.getTimerCount()).toBe(0);
    expect(removeListener).toHaveBeenCalledWith('abort', expect.any(Function));
  });
});
