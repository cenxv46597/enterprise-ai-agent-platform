// 仅用于纯模拟 HTTP 测试；每个工厂均返回独立对象，不包含真实账号或服务数据。
export const USER_ID = '00000000-0000-0000-0000-000000000001';
export const WORKSPACE_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
export const KNOWLEDGE_BASE_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
export const OTHER_KNOWLEDGE_BASE_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbc';
export const CONVERSATION_ID = '11111111-1111-1111-1111-111111111111';
export const REQUEST_ID = '22222222-2222-2222-2222-222222222222';
export const RUN_ID = '33333333-3333-3333-3333-333333333333';
export const DOCUMENT_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
export const OTHER_DOCUMENT_ID = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
export const CHUNK_ID = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
export const OTHER_CHUNK_ID = 'ffffffff-ffff-ffff-ffff-ffffffffffff';
export const BASE_PATH = `/api/v1/workspaces/${WORKSPACE_ID}/knowledge-bases/${KNOWLEDGE_BASE_ID}`;
export const CONVERSATIONS_PATH = `${BASE_PATH}/conversations`;
export const CONVERSATION_PATH = `${CONVERSATIONS_PATH}/${CONVERSATION_ID}`;
export const MESSAGES_PATH = `${CONVERSATION_PATH}/messages`;
export const DOCUMENT_PATH = `${BASE_PATH}/documents/${DOCUMENT_ID}`;
export const OTHER_DOCUMENT_PATH = `${BASE_PATH}/documents/${OTHER_DOCUMENT_ID}`;
export const QUESTION = '请说明资料中的导出规则，并按需要向我追问。';
export const ANSWER = '资料说明导出条目包含文件名称，保留期限为七天。';
export const ABSTENTION = '目前检索到的资料不足以确认这个问题的答案。';
export const RULE = '月费120元，每次上门服务费30元。';
export const PRIVATE = 'PRIVATE_CONVERSATION_INTERNAL_DETAIL';
export const LONG_QUOTE = `每次上门服务费30元。<img src=x onerror="alert(1)">${'🙂'.repeat(305)}完整引文的结尾`;
export const OTHER_QUOTE = '导出条目的保留期限为七天。';

export function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
}

export function createdConversation() {
  return { conversation_id: CONVERSATION_ID, version: 0, status: 'idle', task: null,
    recent_messages: [], billing_result: null };
}

export function documentResponse(id = DOCUMENT_ID) {
  return { id, knowledge_base_id: KNOWLEDGE_BASE_ID,
    original_filename: id === OTHER_DOCUMENT_ID ? '保留说明.md' : '导出说明.md', status: 'ready' };
}

export function evidenceReference(index = 0) {
  const quote = index === 0 ? LONG_QUOTE : OTHER_QUOTE;
  const start = index === 0 ? 100 : 0;
  return { chunk_id: index === 0 ? CHUNK_ID : OTHER_CHUNK_ID,
    document_id: index === 0 ? DOCUMENT_ID : OTHER_DOCUMENT_ID,
    document_name: index === 0 ? '导出说明.md' : '保留说明.md', chunk_index: index + 1,
    start_char: start, end_char: start + Array.from(quote).length };
}

function runEvidence() {
  return [0, 1].map(index => ({ evidence_id: index + 1, ...evidenceReference(index) }));
}

function initialMessage() {
  return { conversation_id: CONVERSATION_ID, request_id: REQUEST_ID, version: 1,
    status: 'idle', reply: '', task: null, execution_deferred: null, billing_result: null,
    answer_preference: null };
}

export function completedReply() {
  return { ...initialMessage(), reply: ANSWER, run: { run_id: RUN_ID, status: 'completed',
    stop_reason: null, steps: [], evidence: runEvidence(), calculations: [], metrics: null,
    result: { answer: ANSWER, abstained: false, reranking: null,
      citations: [0, 1].map(index => ({ ...evidenceReference(index), quote: index === 0 ? LONG_QUOTE : OTHER_QUOTE })) } } };
}

export function abstainedReply() {
  return { ...initialMessage(), reply: ABSTENTION, run: { run_id: RUN_ID, status: 'completed',
    stop_reason: null, steps: [], evidence: [], calculations: [], metrics: null,
    result: { answer: ABSTENTION, abstained: true, citations: [], reranking: null } } };
}

export function waitingReply(question = QUESTION) {
  const clarification = { field_name: 'visit_count', label: '上门次数', unit: '次',
    min_value: 0, max_value: 9999, rule_quote: '每次上门服务费30元', evidence_ids: [1] };
  return { ...initialMessage(), status: 'waiting_for_user', reply: '请问上门服务多少次？',
    task: { question, clarification, confirmed_inputs: [], clarifications: [], billing_plan: null,
      evidence: [evidenceReference(0), evidenceReference(1)] },
    run: { run_id: RUN_ID, status: 'waiting_for_user', stop_reason: null, steps: [], result: null,
      evidence: runEvidence(), calculations: [], metrics: null } };
}

export function billingWaitingReply(question = QUESTION) {
  const reference = { ...evidenceReference(0), end_char: 100 + Array.from(RULE).length };
  const visit = { field_name: 'visit_count', label: '上门次数', unit: '次',
    min_value: 0, max_value: 9999, rule_quote: RULE, evidence_ids: [1] };
  const months = { field_name: 'billing_months', label: '计费月数', unit: '个月',
    min_value: 1, max_value: 12, rule_quote: RULE, evidence_ids: [1] };
  const rate = (quote: string, value: string, unit: string) => ({ ...reference, evidence_id: 1,
    quote, value, unit, start_char: 100 + RULE.indexOf(quote),
    end_char: 100 + RULE.indexOf(quote) + Array.from(quote).length });
  return { ...initialMessage(), status: 'waiting_for_user', reply: '请先提供上门服务次数。',
    task: { question, clarification: visit, confirmed_inputs: [], clarifications: [visit, months], evidence: [reference],
      billing_plan: { workflow: 'monthly_visit_v1', rule_evidence_id: 1, rule_quote: RULE,
        monthly_fee: rate('月费120元', '120', '元/个月'), visit_fee: rate('每次上门服务费30元', '30', '元/次') } },
    run: { run_id: RUN_ID, status: 'waiting_for_user', stop_reason: null, steps: [], result: null,
      evidence: [{ evidence_id: 1, ...reference }], calculations: [], metrics: null } };
}

export function failedReply(status: 'failed' | 'stopped' = 'failed') {
  return { ...initialMessage(), reply: PRIVATE, run: { run_id: RUN_ID, status,
    stop_reason: PRIVATE, steps: [], result: null, evidence: [], calculations: [], metrics: null } };
}

export const CONDITION_REQUEST_ID = '44444444-4444-4444-4444-444444444444';
export const MONTH_REQUEST_ID = '55555555-5555-5555-5555-555555555555';
export function conditionReply(ready = false, count = 3, months = 2) {
  const task = billingWaitingReply().task;
  const confirmed = [{ field_name: 'visit_count', value: count, unit: '次', request_id: CONDITION_REQUEST_ID,
    raw_text: `${count}次`, provenance: 'user_message' }];
  if (ready) confirmed.push({ field_name: 'billing_months', value: months, unit: '个月', request_id: MONTH_REQUEST_ID,
    raw_text: `${months}个月`, provenance: 'user_message' });
  return { conversation_id: CONVERSATION_ID, request_id: ready ? MONTH_REQUEST_ID : CONDITION_REQUEST_ID,
    version: ready ? 3 : 2, status: ready ? 'ready' : 'waiting_for_user', reply: PRIVATE,
    task: { ...task, clarification: task.clarifications[1], confirmed_inputs: confirmed },
    run: null, billing_result: null, answer_preference: null,
    execution_deferred: ready ? 'explicit_execution_required' : null };
}
