import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { createAnswerWorkflow, executeAnswerWorkflow, readAnswerWorkflow, resumeAnswerWorkflow,
  canResumeWorkflow, WorkflowUnconfirmedError, type WorkflowMeta,
  type WorkflowReceipt, type WorkflowResume } from './workflow-api';
import { WORKSPACE_ID, KNOWLEDGE_BASE_ID, OTHER_KNOWLEDGE_BASE_ID, QUESTION, PRIVATE, OTHER_DOCUMENT_ID,
  documentResponse, jsonResponse } from './conversation-test-fixtures';
import { CREATION_REQUEST_ID, WORKFLOW_EXECUTION_REQUEST_ID, WORKFLOW_RESUME_REQUEST_ID, WORKFLOW_EXECUTE_PATH,
  createdWorkflow, failedWorkflow, runningWorkflow, completedWorkflow, workflowExecution, resumedWorkflowExecution } from './workflow-test-fixtures';

const fetchMock = vi.fn<typeof fetch>();
const signal = () => new AbortController().signal;
async function setup(rawFailure = failedWorkflow('generate'), rawCurrent = rawFailure) {
  fetchMock.mockResolvedValueOnce(jsonResponse(createdWorkflow(), 201));
  const created = await createAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID,
    { requestId: CREATION_REQUEST_ID, question: QUESTION }, signal());
  fetchMock.mockResolvedValueOnce(jsonResponse(workflowExecution(rawFailure)));
  if (rawFailure.checkpoint) fetchMock.mockResolvedValueOnce(jsonResponse(documentResponse()))
    .mockResolvedValueOnce(jsonResponse(documentResponse(OTHER_DOCUMENT_ID)));
  const failed = await executeAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID, created,
    { requestId: WORKFLOW_EXECUTION_REQUEST_ID, expectedVersion: 0 }, signal());
  fetchMock.mockResolvedValueOnce(jsonResponse(rawCurrent));
  const current = await readAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID, created, signal());
  fetchMock.mockReset();
  return { created, failed, current };
}
function resume(failed: WorkflowReceipt, current: WorkflowMeta, submitted: WorkflowResume = {
  requestId: WORKFLOW_RESUME_REQUEST_ID, expectedVersion: current.version, confirmed: true,
}, parent = signal()) {
  return resumeAnswerWorkflow(WORKSPACE_ID, KNOWLEDGE_BASE_ID, failed, current, submitted, parent);
}
function success() {
  fetchMock.mockResolvedValueOnce(jsonResponse(resumedWorkflowExecution()))
    .mockResolvedValueOnce(jsonResponse(documentResponse())).mockResolvedValueOnce(jsonResponse(documentResponse(OTHER_DOCUMENT_ID)));
}
beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('已确认生成失败的显式恢复', () => {
  it('先有合法失败回执与匹配GET，绑定新请求和旧版本，正常恢复是第二次尝试与版本5', async () => {
    const { failed, current } = await setup();
    expect(canResumeWorkflow(failed, current)).toBe(true);
    success();
    expect(await resume(failed, current)).toMatchObject({ requestId: WORKFLOW_RESUME_REQUEST_ID,
      attemptNumber: 2, status: 'completed', workflow: { version: 5, attemptCount: 2 }, answer: { abstained: false } });
    expect(fetchMock.mock.calls[0][0]).toBe(WORKFLOW_EXECUTE_PATH);
    expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string)).toEqual({ request_id: WORKFLOW_RESUME_REQUEST_ID,
      expected_version: 3, recover_interrupted: false });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('没有真实GET、凭GET伪造回执、复制/篡改回执、未确认或复用旧编号均不发送', async () => {
    const { failed, current } = await setup();
    for (const [receipt, snapshot, request] of [
      [failed, failed.workflow, { requestId: WORKFLOW_RESUME_REQUEST_ID, expectedVersion: 3, confirmed: true }],
      [{ ...failed }, current, { requestId: WORKFLOW_RESUME_REQUEST_ID, expectedVersion: 3, confirmed: true }],
      [failed, current, { requestId: WORKFLOW_RESUME_REQUEST_ID, expectedVersion: 3, confirmed: false }],
      [failed, current, { requestId: WORKFLOW_EXECUTION_REQUEST_ID, expectedVersion: 3, confirmed: true }],
      [failed, current, { requestId: WORKFLOW_RESUME_REQUEST_ID, expectedVersion: 0, confirmed: true }],
    ] as const) {
      await expect(resume(receipt, snapshot, request as WorkflowResume)).rejects.toBeInstanceOf(ApiError);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['version', 'attempt', 'running', 'completed', 'error', 'checkpoint-body', 'checkpoint-range', 'checkpoint-order'])(
    'GET的%s变化不解锁恢复，块数相同也须核对完整检查点', async change => {
      const current = failedWorkflow('generate');
      switch (change) {
        case 'version': current.version = 4; break;
        case 'attempt': current.attempt_count = 2; break;
        case 'running': Object.assign(current, runningWorkflow('generate')); break;
        case 'completed': Object.assign(current, completedWorkflow()); break;
        case 'error': current.error_code = 'generation_timeout'; break;
        case 'checkpoint-body': current.checkpoint!.hits[1].content = '甲'.repeat(current.checkpoint!.hits[1].content.length); break;
        case 'checkpoint-range': current.checkpoint!.hits[1].start_char += 1; current.checkpoint!.hits[1].end_char += 1; break;
        case 'checkpoint-order': current.checkpoint!.hits.reverse(); break;
      }
      const { failed, current: snapshot } = await setup(failedWorkflow('generate'), current);
      expect(canResumeWorkflow(failed, snapshot)).toBe(false);
      await expect(resume(failed, snapshot)).rejects.toBeInstanceOf(ApiError);
      expect(fetchMock).not.toHaveBeenCalled();
    });

  it('检索失败不纳入本步恢复，也不允许跨知识库提交', async () => {
    const first = await setup(failedWorkflow('retrieve'));
    expect(canResumeWorkflow(first.failed, first.current)).toBe(false);
    await expect(resume(first.failed, first.current)).rejects.toBeInstanceOf(ApiError);
    const second = await setup();
    await expect(resumeAnswerWorkflow(WORKSPACE_ID, OTHER_KNOWLEDGE_BASE_ID, second.failed, second.current,
      { requestId: WORKFLOW_RESUME_REQUEST_ID, expectedVersion: 3, confirmed: true }, signal())).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['request', 'attempt', 'version', 'content', 'source', 'reordered', 'retrieve', 'interrupted'])(
    '恢复回执%s不匹配时不读取来源也不展示答案', async change => {
      const { failed, current } = await setup();
      const body = resumedWorkflowExecution();
      switch (change) {
        case 'request': body.request_id = WORKFLOW_EXECUTION_REQUEST_ID; break;
        case 'attempt': body.attempt_number = 1; break;
        case 'version': body.workflow.version = 6; break;
        case 'content': body.workflow.checkpoint!.hits[1].content = '甲'.repeat(body.workflow.checkpoint!.hits[1].content.length);
          body.workflow.result!.citations[1].quote = body.workflow.checkpoint!.hits[1].content; break;
        case 'source': body.workflow.checkpoint!.hits[0].chunk_index += 1;
          body.workflow.result!.citations[0].chunk_index += 1; break;
        case 'reordered': body.workflow.checkpoint!.hits.reverse(); break;
        case 'retrieve': Object.assign(body, resumedWorkflowExecution(failedWorkflow('retrieve'))); break;
        case 'interrupted': body.status = 'interrupted'; break;
      }
      fetchMock.mockResolvedValueOnce(jsonResponse(body));
      await expect(resume(failed, current)).rejects.toBeInstanceOf(WorkflowUnconfirmedError);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

  it.each(['generation_error', 'evidence_changed', 'checkpoint_invalid'])(
    '第二次尝试合法失败%s如实返回，来源变化清检查点也不自动检索', async code => {
      const { failed, current } = await setup();
      const state = failedWorkflow('generate'); state.error_code = code;
      if (code !== 'generation_error') {
        state.checkpoint = null; state.next_step = 'retrieve'; state.retryable = false;
      }
      fetchMock.mockResolvedValueOnce(jsonResponse(resumedWorkflowExecution(state)));
      if (state.checkpoint) fetchMock.mockResolvedValueOnce(jsonResponse(documentResponse()))
        .mockResolvedValueOnce(jsonResponse(documentResponse(OTHER_DOCUMENT_ID)));
      const reply = await resume(failed, current);
      expect(reply).toMatchObject({ status: 'failed', attemptNumber: 2, workflow: { version: 5, errorCode: code } });
      expect(canResumeWorkflow(reply, reply.workflow)).toBe(false);
      expect(fetchMock).toHaveBeenCalledTimes(state.checkpoint ? 3 : 1);
    });

  it.each([401, 403, 404, 409, 422, 503])('HTTP%s不自动再恢复且不回显detail', async status => {
    const { failed, current } = await setup();
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, status));
    const error = await resume(failed, current).catch(error => error);
    expect(error).toBeInstanceOf(ApiError); expect(String(error)).not.toContain(PRIVATE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('所有来源核对失败时整体未确认；401保留登录失效语义', async () => {
    const { failed, current } = await setup();
    fetchMock.mockResolvedValueOnce(jsonResponse(resumedWorkflowExecution()))
      .mockResolvedValueOnce(jsonResponse(documentResponse())).mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, 401));
    const error = await resume(failed, current).catch(error => error);
    expect(error).toBeInstanceOf(ApiError); expect(error.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('恢复630秒超时的迟到回执不再读取来源，保持恢复未确认', async () => {
    const { failed, current } = await setup(); vi.useFakeTimers();
    let resolve!: (value: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>(done => { resolve = done; }));
    const action = resume(failed, current).catch(error => error);
    await vi.advanceTimersByTimeAsync(630_000);
    expect(fetchMock.mock.calls[0][1]!.signal!.aborted).toBe(true);
    resolve(jsonResponse(resumedWorkflowExecution()));
    expect(await action).toMatchObject({ name: 'WorkflowUnconfirmedError', stage: 'resume' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
