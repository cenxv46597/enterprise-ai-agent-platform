// 固定问答工作流的纯模拟数据；不含真实账号、模型或数据库记录。
import {
  ABSTENTION, ANSWER, BASE_PATH, KNOWLEDGE_BASE_ID, LONG_QUOTE, OTHER_QUOTE,
  QUESTION, WORKSPACE_ID, evidenceReference,
} from './conversation-test-fixtures';

export const WORKFLOW_ID = '77777777-7777-7777-7777-777777777777';
export const CREATION_REQUEST_ID = '88888888-8888-8888-8888-888888888888';
export const WORKFLOW_EXECUTION_REQUEST_ID = '99999999-9999-9999-9999-999999999999';
export const WORKFLOWS_PATH = `${BASE_PATH}/answer-workflows`;
export const WORKFLOW_PATH = `${WORKFLOWS_PATH}/${WORKFLOW_ID}`;
export const WORKFLOW_EXECUTE_PATH = `${WORKFLOW_PATH}/execute`;

export function workflowCheckpoint() {
  return { schema_version: 1, workspace_id: WORKSPACE_ID, knowledge_base_id: KNOWLEDGE_BASE_ID,
    strategy: 'vector', hits: [0, 1].map(index => ({ ...evidenceReference(index),
      content: index === 0 ? LONG_QUOTE : OTHER_QUOTE, distance: index === 0 ? 0.1 : 0.2,
      fusion_score: null, vector_rank: null, lexical_rank: null, lexical_score: null,
      retrieval_rank: null, rerank_rank: null })) };
}

export function createdWorkflow(question = QUESTION) {
  return { workflow_id: WORKFLOW_ID, workspace_id: WORKSPACE_ID, knowledge_base_id: KNOWLEDGE_BASE_ID,
    question, strategy: 'vector', max_context_characters: 12000, status: 'pending', next_step: 'retrieve',
    version: 0, attempt_count: 0, checkpoint: null as ReturnType<typeof workflowCheckpoint> | null,
    error_code: null as string | null, retryable: false, active_request_id: null as string | null,
    lease_expires_at: null as string | null,
    result: null as { answer: string; abstained: boolean; citations: Array<ReturnType<typeof evidenceReference> & { quote: string }>;
      reranking: null } | null };
}

export function completedWorkflow(question = QUESTION) {
  return { ...createdWorkflow(question), status: 'completed', next_step: 'done', version: 3,
    attempt_count: 1, checkpoint: workflowCheckpoint(), result: { answer: ANSWER, abstained: false,
      citations: [0, 1].map(index => ({ ...evidenceReference(index), quote: index === 0 ? LONG_QUOTE : OTHER_QUOTE })),
      reranking: null } };
}

export function abstainedWorkflow(question = QUESTION) {
  return { ...completedWorkflow(question), checkpoint: { ...workflowCheckpoint(), hits: [] },
    result: { answer: ABSTENTION, abstained: true, citations: [], reranking: null } };
}

export function failedWorkflow(stage: 'retrieve' | 'generate' = 'retrieve', question = QUESTION) {
  return { ...createdWorkflow(question), status: 'failed', next_step: stage, version: stage === 'retrieve' ? 2 : 3,
    attempt_count: 1, checkpoint: stage === 'retrieve' ? null : workflowCheckpoint(),
    error_code: stage === 'retrieve' ? 'embedding_timeout' : 'generation_error', retryable: true };
}

export function runningWorkflow(step: 'retrieve' | 'generate' = 'retrieve', question = QUESTION) {
  return { ...createdWorkflow(question), status: 'running', next_step: step, version: step === 'retrieve' ? 1 : 2,
    attempt_count: 1, checkpoint: step === 'retrieve' ? null : workflowCheckpoint(),
    active_request_id: WORKFLOW_EXECUTION_REQUEST_ID, lease_expires_at: '2026-10-17T12:30:00Z' };
}

export function workflowExecution(workflow: ReturnType<typeof createdWorkflow> = completedWorkflow()) {
  return { request_id: WORKFLOW_EXECUTION_REQUEST_ID, attempt_number: 1,
    status: workflow.status === 'completed' ? 'completed' : 'failed', workflow };
}
