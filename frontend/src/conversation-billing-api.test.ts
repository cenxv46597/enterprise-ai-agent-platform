import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { BILLING_EXECUTION_CONTENT, ConversationUnconfirmedError, executeConversationBilling,
  sendFirstConversationMessage, submitConversationCondition, formatBillingMoney,
  type ConversationConditions, type BillingExecutionRequest } from './conversation-api';
import { WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID, REQUEST_ID, CONDITION_REQUEST_ID, MONTH_REQUEST_ID,
  EXECUTION_REQUEST_ID, MESSAGES_PATH, QUESTION, PRIVATE, jsonResponse, documentResponse,
  billingWaitingReply, conditionReply, billingExecutionReply } from './conversation-test-fixtures';

const fetchMock = vi.fn<typeof fetch>();
const signal = () => new AbortController().signal;
async function ready(count = 3, months = 2, monthly = '120', visit = '30'): Promise<ConversationConditions> {
  fetchMock.mockResolvedValueOnce(jsonResponse(billingWaitingReply(QUESTION, monthly, visit)))
    .mockResolvedValueOnce(jsonResponse(documentResponse()));
  const first = await sendFirstConversationMessage(WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID,
    { requestId: REQUEST_ID, expectedVersion: 0, content: QUESTION }, signal());
  let state: ConversationConditions = { version: 1, status: 'waiting_for_user', pending: first.pending!, inputs: [] };
  for (const [value, requestId, response] of [
    [String(count), CONDITION_REQUEST_ID, conditionReply(false, count, months, monthly, visit)],
    [String(months), MONTH_REQUEST_ID, conditionReply(true, count, months, monthly, visit)],
  ] as const) {
    fetchMock.mockResolvedValueOnce(jsonResponse(response)).mockResolvedValueOnce(jsonResponse(documentResponse()));
    state = await submitConversationCondition(WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID,
      state, { requestId, expectedVersion: state.version, value }, signal());
  }
  fetchMock.mockReset();
  return state;
}
function execute(previous: ConversationConditions, request: BillingExecutionRequest = {
  requestId: EXECUTION_REQUEST_ID, expectedVersion: previous.version, confirmed: true,
}, parent = signal()) {
  return executeConversationBilling(WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID, previous, request, parent);
}
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('标准规则显式估算回执', () => {
  it('固定execute/true，使用ready版本及新UUID；结果与任务双重核对后才读取来源', async () => {
    const state = await ready();
    fetchMock.mockResolvedValueOnce(jsonResponse(billingExecutionReply())).mockResolvedValueOnce(jsonResponse(documentResponse()));
    expect(await execute(state)).toMatchObject({ version: 4, monthlySubtotal: '240', visitSubtotal: '90', total: '330',
      visits: { value: 3 }, months: { value: 2 } });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe(MESSAGES_PATH);
    expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string)).toEqual({ request_id: EXECUTION_REQUEST_ID,
      expected_version: 3, content: BILLING_EXECUTION_CONTENT, mode: 'execute', confirm_billing_rule: true, response_mode: 'knowledge' });
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', cache: 'no-store', redirect: 'error' });
  });

  it('未确认、未ready、旧单字段、旧版本及复用条件请求编号均本地拒绝', async () => {
    const state = await ready();
    for (const [previous, submitted] of [
      [state, { confirmed: false, expectedVersion: 3, requestId: EXECUTION_REQUEST_ID }],
      [{ ...state, status: 'waiting_for_user' }, { confirmed: true, expectedVersion: 3, requestId: EXECUTION_REQUEST_ID }],
      [{ ...state, pending: { ...state.pending, kind: 'count' } }, { confirmed: true, expectedVersion: 3, requestId: EXECUTION_REQUEST_ID }],
      [state, { confirmed: true, expectedVersion: 2, requestId: EXECUTION_REQUEST_ID }],
      [state, { confirmed: true, expectedVersion: 3, requestId: MONTH_REQUEST_ID }],
    ]) await expect(execute(previous as ConversationConditions, submitted as BillingExecutionRequest)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['version', 'request', 'status', 'run', 'deferred', 'preference', 'scope', 'currency', 'plan', 'condition',
    'confirmation', 'subtotal', 'total', 'step-count', 'step-ref', 'step-value', 'step-unit', 'step-provenance',
    'task-result', 'task-rule', 'task-input', 'task-source'])(
    '拒绝%s不匹配的200，不读取来源也不声称完成', async fault => {
      const state = await ready();
      const payload = billingExecutionReply();
      switch (fault) {
        case 'version': payload.version = 5; break;
        case 'request': payload.request_id = MONTH_REQUEST_ID; break;
        case 'status': payload.status = 'ready'; break;
        case 'run': Object.assign(payload, { run: {} }); break;
        case 'deferred': Object.assign(payload, { execution_deferred: 'explicit_execution_required' }); break;
        case 'preference': Object.assign(payload, { answer_preference: {} }); break;
        case 'scope': payload.billing_result.scope = PRIVATE; break;
        case 'currency': payload.billing_result.currency = 'USD'; break;
        case 'plan': payload.billing_result.plan.monthly_fee.value = '121'; break;
        case 'condition': payload.billing_result.visit_count.value = 4; break;
        case 'confirmation': payload.billing_result.confirmation_request_id = MONTH_REQUEST_ID; break;
        case 'subtotal': payload.billing_result.monthly_subtotal = '241'; break;
        case 'total': payload.billing_result.total = '331'; break;
        case 'step-count': payload.billing_result.steps.pop(); break;
        case 'step-ref': payload.billing_result.steps[0].right_ref = 'visit_count'; break;
        case 'step-value': payload.billing_result.steps[0].value = '241'; break;
        case 'step-unit': payload.billing_result.steps[0].unit = '次'; break;
        case 'step-provenance': payload.billing_result.steps[0].provenance = 'model'; break;
        case 'task-result': payload.task.billing_result.total = '331'; break;
        case 'task-rule': payload.task.question = PRIVATE; break;
        case 'task-input': payload.task.confirmed_inputs[0].raw_text = '4次'; break;
        case 'task-source': payload.task.evidence[0].chunk_index = 9; break;
      }
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      await expect(execute(state)).rejects.toBeInstanceOf(ConversationUnconfirmedError);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

  it.each([401, 403, 404, 409, 422, 502])('HTTP%s不重试，不回显内部detail', async status => {
    const state = await ready();
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, status));
    const error = await execute(state).catch(error => error);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(status);
    expect(String(error)).not.toContain(PRIVATE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([401, 404, 200])('来源核对%s失败时不能展示已保存结果', async status => {
    const state = await ready();
    fetchMock.mockResolvedValueOnce(jsonResponse(billingExecutionReply())).mockResolvedValueOnce(status === 200
      ? jsonResponse({ ...documentResponse(), original_filename: PRIVATE }) : jsonResponse({ detail: PRIVATE }, status));
    const error = await execute(state).catch(error => error);
    expect(error).toBeInstanceOf(status === 401 ? ApiError : ConversationUnconfirmedError);
    if (status === 401) expect(error.status).toBe(401);
    expect(String(error)).not.toContain(PRIVATE);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('小数费率使用已确认月份，0次服务小计正确为0', async () => {
    const state = await ready(0, 6, '80.50', '25.25');
    fetchMock.mockResolvedValueOnce(jsonResponse(billingExecutionReply(0, 6, '80.50', '25.25', '483', '0', '483')))
      .mockResolvedValueOnce(jsonResponse(documentResponse()));
    const result = await execute(state);
    expect(result.total).toBe('483');
    expect(result.visitSubtotal).toBe('0');
    expect(formatBillingMoney(result.total)).toBe('483.00');
  });

  it('上限金额的分超过Number安全整数时仍精确到分', async () => {
    const state = await ready(9999, 12, '999999999999.99', '999999999999.99');
    fetchMock.mockResolvedValueOnce(jsonResponse(billingExecutionReply(9999, 12, '999999999999.99', '999999999999.99',
      '11999999999999.88', '9998999999999900.01', '10010999999999899.89')))
      .mockResolvedValueOnce(jsonResponse(documentResponse()));
    expect((await execute(state)).total).toBe('10010999999999899.89');
  });

  it('十秒超时及迟到200不自动执行第二次', async () => {
    const state = await ready();
    vi.useFakeTimers();
    let resolve!: (value: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>(done => { resolve = done; }));
    const action = execute(state).catch(error => error);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchMock.mock.calls[0][1]!.signal!.aborted).toBe(true);
    resolve(jsonResponse(billingExecutionReply()));
    expect(await action).toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
