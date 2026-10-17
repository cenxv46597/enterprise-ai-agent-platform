import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { sendFirstConversationMessage, submitConversationCondition, ConversationUnconfirmedError,
  type ConversationConditions } from './conversation-api';
import { WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID, REQUEST_ID, CONDITION_REQUEST_ID, MONTH_REQUEST_ID,
  QUESTION, MESSAGES_PATH, PRIVATE, documentResponse, billingWaitingReply, conditionReply, waitingReply,
  jsonResponse } from './conversation-test-fixtures';

const fetchMock = vi.fn<typeof fetch>();
const signal = () => new AbortController().signal;
async function initial(billing = true): Promise<ConversationConditions> {
  fetchMock.mockResolvedValueOnce(jsonResponse(billing ? billingWaitingReply() : waitingReply()));
  fetchMock.mockResolvedValue(jsonResponse(documentResponse()));
  if (!billing) fetchMock.mockResolvedValueOnce(jsonResponse(documentResponse()))
    .mockResolvedValueOnce(jsonResponse(documentResponse('dddddddd-dddd-dddd-dddd-dddddddddddd')));
  const reply = await sendFirstConversationMessage(WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID,
    { requestId: REQUEST_ID, expectedVersion: 0, content: QUESTION }, signal());
  fetchMock.mockReset();
  return { version: 1, status: 'waiting_for_user', pending: reply.pending!, inputs: [] };
}
function submit(previous: ConversationConditions, value = '3', requestId = CONDITION_REQUEST_ID,
  parent = signal()) {
  return submitConversationCondition(WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID,
    previous, { expectedVersion: previous.version, requestId, value }, parent);
}
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('逐项补充条件的保存回执', () => {
  it('上门次数保存为版本2，再保存计费月份为ready版本3；固定continue且不执行', async () => {
    const previous = await initial();
    fetchMock.mockResolvedValueOnce(jsonResponse(conditionReply())).mockResolvedValueOnce(jsonResponse(documentResponse()));
    const next = await submit(previous);
    expect(next).toMatchObject({ version: 2, status: 'waiting_for_user',
      pending: { current: { fieldName: 'billing_months' } }, inputs: [{ value: 3, unit: '次' }] });
    expect(fetchMock.mock.calls[0][0]).toBe(MESSAGES_PATH);
    expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string)).toEqual({ request_id: CONDITION_REQUEST_ID,
      expected_version: 1, content: '3次', mode: 'continue', confirm_billing_rule: false, response_mode: 'knowledge' });
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', redirect: 'error', cache: 'no-store' });
    fetchMock.mockResolvedValueOnce(jsonResponse(conditionReply(true))).mockResolvedValueOnce(jsonResponse(documentResponse()));
    const ready = await submit(next, '2', MONTH_REQUEST_ID);
    expect(ready).toMatchObject({ version: 3, status: 'ready', inputs: [{ value: 3 }, { value: 2 }] });
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(JSON.parse(fetchMock.mock.calls[2][1]!.body as string).expected_version).toBe(2);
    await expect(submit(ready, '2', REQUEST_ID)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('0次有效；空白、小数、负数、指数、汉字、单位混填和超界值不发送', async () => {
    const previous = await initial();
    for (const value of ['', ' ', '-1', '1.5', '1e2', '三', '3次', '01', '10000', '3\n', ' 3']) {
      await expect(submit(previous, value)).rejects.toBeInstanceOf(ApiError);
    }
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(jsonResponse(conditionReply(false, 0))).mockResolvedValueOnce(jsonResponse(documentResponse()));
    const next = await submit(previous, '0');
    expect(next.inputs[0].value).toBe(0);
    fetchMock.mockReset();
    for (const value of ['0', '13']) await expect(submit(next, value, MONTH_REQUEST_ID)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('兼容旧单字段任务，保存一次即ready，核对包括非规则候选在内的全部来源', async () => {
    const previous = await initial(false);
    const base = waitingReply();
    const single = { ...conditionReply(), status: 'ready', execution_deferred: 'explicit_execution_required',
      task: { ...base.task, confirmed_inputs: conditionReply().task.confirmed_inputs } };
    fetchMock.mockResolvedValueOnce(jsonResponse(single)).mockResolvedValueOnce(jsonResponse(documentResponse()))
      .mockResolvedValueOnce(jsonResponse(documentResponse('dddddddd-dddd-dddd-dddd-dddddddddddd')));
    expect(await submit(previous)).toMatchObject({ status: 'ready', version: 2, inputs: [{ value: 3 }] });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it.each(['version', 'request', 'value', 'unit', 'field', 'raw', 'provenance', 'rule', 'source', 'current',
    'status', 'deferred', 'run', 'billing', 'missing-input'])(
    '拒绝%s不匹配的200回执，不推进任务也不读来源', async fault => {
      const previous = await initial();
      const payload = conditionReply();
      switch (fault) {
        case 'version': payload.version = 3; break;
        case 'request': payload.request_id = MONTH_REQUEST_ID; break;
        case 'value': payload.task.confirmed_inputs[0].value = 9; break;
        case 'unit': payload.task.confirmed_inputs[0].unit = '个月'; break;
        case 'field': payload.task.confirmed_inputs[0].field_name = 'billing_months'; break;
        case 'raw': payload.task.confirmed_inputs[0].raw_text = '9次'; break;
        case 'provenance': payload.task.confirmed_inputs[0].provenance = 'model'; break;
        case 'rule': payload.task.clarifications[0].rule_quote = PRIVATE; break;
        case 'source': payload.task.evidence[0].chunk_index = 99; break;
        case 'current': payload.task.clarification = payload.task.clarifications[0]; break;
        case 'status': payload.status = 'ready'; break;
        case 'deferred': Object.assign(payload, { execution_deferred: 'explicit_execution_required' }); break;
        case 'run': Object.assign(payload, { run: {} }); break;
        case 'billing': Object.assign(payload, { billing_result: {} }); break;
        case 'missing-input': payload.task.confirmed_inputs = []; break;
      }
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      await expect(submit(previous)).rejects.toBeInstanceOf(ConversationUnconfirmedError);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

  it('第二项不能篡改之前已确认的次数；ready必须明确延后执行', async () => {
    const previous = await initial();
    fetchMock.mockResolvedValueOnce(jsonResponse(conditionReply())).mockResolvedValueOnce(jsonResponse(documentResponse()));
    const next = await submit(previous);
    fetchMock.mockReset();
    const changed = conditionReply(true);
    changed.task.confirmed_inputs[0].value = 8;
    fetchMock.mockResolvedValueOnce(jsonResponse(changed));
    await expect(submit(next, '2', MONTH_REQUEST_ID)).rejects.toBeInstanceOf(ConversationUnconfirmedError);
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...conditionReply(true), execution_deferred: null }));
    await expect(submit(next, '2', MONTH_REQUEST_ID)).rejects.toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([401, 404, 409, 422, 502])('HTTP%s只请求一次，使用安全提示', async status => {
    const previous = await initial();
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, status));
    const failure = await submit(previous).catch(error => error as ApiError);
    expect(failure).toBeInstanceOf(ApiError);
    expect(String(failure)).not.toContain(PRIVATE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('保存响应后仍须核对来源；来源401保留登录失效，404或改名则整体未确认', async () => {
    const previous = await initial();
    for (const status of [401, 404, 200]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(conditionReply())).mockResolvedValueOnce(
        status === 200 ? jsonResponse({ ...documentResponse(), original_filename: PRIVATE })
          : jsonResponse({ detail: PRIVATE }, status));
      const failure = await submit(previous).catch(error => error as ApiError);
      expect(failure).toBeInstanceOf(status === 401 ? ApiError : ConversationUnconfirmedError);
      if (status === 401) expect(failure.status).toBe(401);
      expect(String(failure)).not.toContain(PRIVATE);
    }
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });

  it('十秒超时的迟到200不能推进任务，不自动重试', async () => {
    const previous = await initial();
    vi.useFakeTimers();
    let resolve!: (response: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>(done => { resolve = done; }));
    const action = submit(previous).catch(error => error);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchMock.mock.calls[0][1]!.signal!.aborted).toBe(true);
    resolve(jsonResponse(conditionReply()));
    expect(await action).toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
