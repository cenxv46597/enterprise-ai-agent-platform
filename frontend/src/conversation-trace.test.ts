import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sendFirstConversationMessage, ConversationUnconfirmedError } from './conversation-api';
import { parseConversationTrace } from './conversation-trace';
import { WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID, REQUEST_ID, QUESTION, PRIVATE,
  tracedReply, publicTraceStep, documentResponse, OTHER_DOCUMENT_ID, jsonResponse, completedReply } from './conversation-test-fixtures';

const fetchMock = vi.fn<typeof fetch>();
function send() {
  return sendFirstConversationMessage(WORKSPACE_ID, KNOWLEDGE_BASE_ID, CONVERSATION_ID,
    { requestId: REQUEST_ID, expectedVersion: 0, content: QUESTION }, new AbortController().signal);
}
const sources = completedReply().run.evidence.map(item => ({ evidenceId: item.evidence_id,
  chunkId: item.chunk_id, documentId: item.document_id, documentName: item.document_name,
  chunkIndex: item.chunk_index, startChar: item.start_char, endChar: item.end_char }));
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe('公开运行轨迹的有界投影', () => {
  it('拒绝参数未执行，之后检索成功；真实统计不按工具记录条数推算', () => {
    const trace = parseConversationTrace(tracedReply().run, sources, 0);
    expect(trace).toMatchObject({ kind: 'available', metrics: { decisionCount: 3, toolExecutionCount: 1 },
      steps: [{ execution: 'blocked', reason: '工具参数未通过校验，未进入执行' },
        { name: '知识库检索', execution: 'entered', evidence: sources }, { action: 'finish' }] });
    expect(JSON.stringify(trace)).not.toContain(PRIVATE);
  });

  it.each(['duplicate_action', 'step_limit'])('%s记录不当作实际执行，零耗时不作为执行依据', code => {
    const run = { steps: [{ ...publicTraceStep(), status: 'stopped', error_code: code, evidence_ids: [], execution_ms: 0 }],
      metrics: { total_ms: 2, decision_count: 1, tool_execution_count: 0 }, stop_reason: code };
    expect(parseConversationTrace(run, sources, 0)).toMatchObject({ kind: 'available',
      steps: [{ execution: 'blocked' }], metrics: { toolExecutionCount: 0 } });
  });

  it('工具执行超时仍算进入执行；结束生成失败是另一个步骤，不把运行当完成', () => {
    const run = { steps: [
      { ...publicTraceStep(), status: 'error', error_code: 'tool_timeout', evidence_ids: [], execution_ms: 0 },
      { ...publicTraceStep(2), action: 'finish', tool_name: null, status: 'error', error_code: 'generation_error', evidence_ids: [] },
    ], metrics: { total_ms: 20, decision_count: 2, tool_execution_count: 1 }, stop_reason: 'generation_error' };
    expect(parseConversationTrace(run, sources, 0)).toMatchObject({ kind: 'available',
      steps: [{ execution: 'entered', reason: '工具执行超时' }, { execution: 'not-tool', reason: '回答生成失败' }] });
  });

  it('未知名称及错误仅固定提示；即使耗时为正也不猜测执行，旧记录缺统计保持null', () => {
    const trace = parseConversationTrace({ steps: [{ ...publicTraceStep(), tool_name: PRIVATE,
      status: 'error', error_code: PRIVATE, evidence_ids: [], execution_ms: 5 }], stop_reason: PRIVATE }, sources, 0);
    expect(trace).toMatchObject({ kind: 'available', metrics: null, reason: '原因未识别，未展示原始内容',
      steps: [{ name: '其他工具（名称未展示）', execution: 'unknown' }] });
    expect(JSON.stringify(trace)).not.toContain(PRIVATE);
    expect(parseConversationTrace({ steps: [], metrics: null }, sources, 0)).toMatchObject({ kind: 'available', steps: [], metrics: null });
  });

  it('旧步骤缺少阶段耗时不补为0，显式null不是合法耗时', () => {
    const { execution_ms: _missing, ...oldStep } = publicTraceStep();
    expect(parseConversationTrace({ steps: [oldStep] }, sources, 0)).toMatchObject({ kind: 'available', steps: [{ executionMs: null }] });
    expect(parseConversationTrace({ steps: [{ ...oldStep, execution_ms: null }] }, sources, 0)).toEqual({ kind: 'unavailable' });
  });

  it.each(['too-many', 'step-order', 'action', 'status', 'tool', 'error', 'evidence', 'duplicate-evidence',
    'calculation', 'negative-time', 'nan-time', 'metric-count', 'metric-tool-count', 'metric-infinite', 'metric-negative',
    'blocked-evidence', 'finish-evidence', 'long-name'])(
    '格式错误%s只禁用轨迹，不生成伪造步骤或统计', fault => {
      const run = tracedReply().run;
      switch (fault) {
        case 'too-many': run.steps = Array.from({ length: 9 }, (_, i) => publicTraceStep(i + 1)); break;
        case 'step-order': run.steps[0].step = 2; break;
        case 'action': run.steps[0].action = 'execute'; break;
        case 'status': run.steps[0].status = 'completed'; break;
        case 'tool': run.steps[0].tool_name = null; break;
        case 'error': run.steps[1].error_code = 'tool_error'; break;
        case 'evidence': run.steps[1].evidence_ids = [3]; break;
        case 'duplicate-evidence': run.steps[1].evidence_ids = [1, 1]; break;
        case 'calculation': run.steps[1].calculation_id = 1; break;
        case 'negative-time': run.steps[1].planner_ms = -1; break;
        case 'nan-time': run.steps[1].execution_ms = NaN; break;
        case 'metric-count': run.metrics.decision_count = 4; break;
        case 'metric-tool-count': run.metrics.tool_execution_count = 2; break;
        case 'metric-infinite': run.metrics.total_ms = Infinity; break;
        case 'metric-negative': run.metrics.tool_execution_count = -1; break;
        case 'blocked-evidence': run.steps[0].evidence_ids = [1]; break;
        case 'finish-evidence': run.steps[2].evidence_ids = [1]; break;
        case 'long-name': run.steps[1].tool_name = 'a'.repeat(81); break;
      }
      expect(parseConversationTrace(run, sources, 0)).toEqual({ kind: 'unavailable' });
    });

  it('算术记录只接受本轮已有编号，不显示未经复核的金额', () => {
    const trace = parseConversationTrace({ steps: [{ ...publicTraceStep(), tool_name: 'calculator', calculation_id: 1,
      evidence_ids: [], execution_ms: 0 }] }, sources, 1);
    expect(trace).toMatchObject({ kind: 'available', metrics: null, steps: [{ calculationId: 1 }] });
  });

  it('拒绝运行结束状态与最后一步不一致，以及结束步骤后继续执行的轨迹', () => {
    for (const status of ['failed', 'stopped', 'waiting_for_user']) {
      expect(parseConversationTrace({ ...tracedReply().run, status }, sources, 0)).toEqual({ kind: 'unavailable' });
    }
    const run = tracedReply().run;
    run.steps = [run.steps[2], publicTraceStep(2)];
    run.steps[0].step = 1;
    expect(parseConversationTrace(run, sources, 0)).toEqual({ kind: 'unavailable' });
  });

  it('轨迹格式无效不丢弃独立核对通过的答案', async () => {
    const payload = tracedReply(); payload.run.steps[0].step = 2;
    fetchMock.mockResolvedValueOnce(jsonResponse(payload)).mockResolvedValueOnce(jsonResponse(documentResponse()))
      .mockResolvedValueOnce(jsonResponse(documentResponse(OTHER_DOCUMENT_ID)));
    const reply = await send();
    expect(reply.kind).toBe('answered');
    expect(reply.answer?.abstained).toBe(false);
    expect(reply.trace).toEqual({ kind: 'unavailable' });
  });

  it('轨迹关联了最终答案未引用的候选时，也先核对全部来源', async () => {
    const payload = tracedReply(); payload.run.result.citations = [payload.run.result.citations[0]];
    fetchMock.mockResolvedValueOnce(jsonResponse(payload)).mockResolvedValueOnce(jsonResponse(documentResponse()))
      .mockResolvedValueOnce(jsonResponse({ detail: PRIVATE }, 404));
    await expect(send()).rejects.toBeInstanceOf(ConversationUnconfirmedError);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
