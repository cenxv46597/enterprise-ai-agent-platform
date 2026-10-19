import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, listDocuments, listKnowledgeBases, listWorkspaces, login, logout, restoreSession,
  uploadDocument, UploadUnconfirmedError, UPLOAD_UNCONFIRMED_MESSAGE,
  getDocument, listDocumentChunks, createDocumentChunks, ChunkUnconfirmedError, CHUNK_UNCONFIRMED_MESSAGE,
  createDocumentEmbeddings, EmbeddingUnconfirmedError, EMBEDDING_UNCONFIRMED_MESSAGE,
  searchKnowledgeBase, SearchUnconfirmedError, SEARCH_UNCONFIRMED_MESSAGE,
  answerKnowledgeBase, AnswerUnconfirmedError, ANSWER_UNCONFIRMED_MESSAGE,
  type DocumentChunk, type KnowledgeBase, type KnowledgeDocument, type Session, type Workspace } from './api';
import DocumentList from './DocumentList';
import DocumentUpload, { type UploadOutcome } from './DocumentUpload';
import DocumentProcessing, { type ChunkOutcome } from './DocumentProcessing';
import DocumentEmbedding, { type EmbeddingOutcome } from './DocumentEmbedding';
import KnowledgeSearch, { type SearchOutcome } from './KnowledgeSearch';
import KnowledgeAnswer, { type AnswerOutcome } from './KnowledgeAnswer';
import KnowledgeConversation, { type ConversationView } from './KnowledgeConversation';
import KnowledgeWorkflow, { type WorkflowView } from './KnowledgeWorkflow';
import WorkflowHistory, { type WorkflowHistoryView } from './WorkflowHistory';
import { listAnswerWorkflows, type WorkflowHistoryItem } from './workflow-history-api';
import { createAnswerWorkflow, executeAnswerWorkflow, readAnswerWorkflow, WorkflowUnconfirmedError,
  resumeAnswerWorkflow, canResumeWorkflow, sameWorkflowMeta, canReadSavedWorkflowAnswer, readSavedWorkflowAnswer,
  WORKFLOW_NOTICES, type WorkflowCreation, type WorkflowExecution, type WorkflowResume } from './workflow-api';
import {
  createConversation, sendFirstConversationMessage, getConversationSnapshot, ConversationUnconfirmedError,
  CONVERSATION_CREATE_UNCONFIRMED_MESSAGE, CONVERSATION_MESSAGE_UNCONFIRMED_MESSAGE,
  type FirstMessageRequest,
  submitConversationCondition, CONDITION_UNCONFIRMED_MESSAGE, type ConditionRequest,
  executeConversationBilling, BILLING_UNCONFIRMED_MESSAGE, type BillingExecutionRequest,
} from './conversation-api';

type Busy = 'restore' | 'login' | 'workspaces' | 'knowledge-bases' | 'documents' | 'upload'
  | 'document-detail' | 'chunking' | 'embedding' | 'search' | 'answer'
  | 'conversation-create' | 'conversation-message' | 'conversation-supplement' | 'conversation-execute'
  | 'conversation-read' | 'workflow-create' | 'workflow-execute' | 'workflow-resume' | 'workflow-read'
  | 'workflow-answer-read' | 'workflow-history-list' | 'workflow-history-read' | 'workflow-history-answer' | 'logout' | null;
type Phase = 'restoring' | 'restore-error' | 'login' | 'workspace';

export default function App() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [session, setSession] = useState<Session | null>(null);
  const [phase, setPhase] = useState<Phase>('restoring');
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedWorkspace, setSelectedWorkspace] = useState<Workspace | null>(null);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKnowledgeBase, setSelectedKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [uploadOutcome, setUploadOutcome] = useState<UploadOutcome | null>(null);
  const [selectedDocument, setSelectedDocument] = useState<KnowledgeDocument | null>(null);
  const [documentDetail, setDocumentDetail] = useState<KnowledgeDocument | null>(null);
  const [chunks, setChunks] = useState<DocumentChunk[] | null>(null);
  const [chunkOutcome, setChunkOutcome] = useState<ChunkOutcome | null>(null);
  const [embeddingOutcome, setEmbeddingOutcome] = useState<EmbeddingOutcome | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchOutcome, setSearchOutcome] = useState<SearchOutcome | null>(null);
  const [answerOpen, setAnswerOpen] = useState(false);
  const [answerOutcome, setAnswerOutcome] = useState<AnswerOutcome | null>(null);
  const [conversationView, setConversationView] = useState<ConversationView | null>(null);
  const [workflowView, setWorkflowView] = useState<WorkflowView | null>(null);
  const [workflowHistoryView, setWorkflowHistoryView] = useState<WorkflowHistoryView | null>(null);
  const [busy, setBusy] = useState<Busy>('restore');
  const [error, setError] = useState('');
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);

  const reset = useCallback((message = '', next: Phase = 'login') => {
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    setSession(null);
    setPhase(next);
    setWorkspaces([]);
    setSelectedWorkspace(null);
    setKnowledgeBases([]);
    setSelectedKnowledgeBase(null);
    setDocuments([]);
    setUploadOutcome(null);
    setSelectedDocument(null);
    setDocumentDetail(null);
    setChunks(null);
    setChunkOutcome(null);
    setEmbeddingOutcome(null);
    setSearchOpen(false);
    setSearchOutcome(null);
    setAnswerOpen(false);
    setAnswerOutcome(null);
    setConversationView(null);
    setWorkflowView(null);
    setWorkflowHistoryView(null);
    setPassword('');
    setBusy(null);
    setError(message);
  }, []);

  useEffect(() => {
    void restore();
    return () => {
      generation.current += 1;
      pending.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(
      () => reset('登录已到期，请重新登录。'),
      Math.max(0, session.expiresAt - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [session, reset]);

  function beginRequest() {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    return { id: ++generation.current, controller };
  }

  function isCurrent(id: number, controller: AbortController) {
    return id === generation.current && !controller.signal.aborted;
  }

  async function refresh() {
    const { id, controller } = beginRequest();
    setBusy('workspaces');
    setError('');
    setWorkspaces([]);
    try {
      const rows = await listWorkspaces(controller.signal);
      if (isCurrent(id, controller)) setWorkspaces(rows);
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setError(failure instanceof ApiError ? failure.message : '无法读取工作空间，请稍后重试。');
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function restore() {
    const { id, controller } = beginRequest();
    setPhase('restoring');
    setBusy('restore');
    setError('');
    setSession(null);
    setWorkspaces([]);
    setSelectedWorkspace(null);
    setKnowledgeBases([]);
    setSelectedKnowledgeBase(null);
    setDocuments([]);
    setUploadOutcome(null);
    setSelectedDocument(null);
    setDocumentDetail(null);
    setChunks(null);
    setChunkOutcome(null);
    setEmbeddingOutcome(null);
    setSearchOpen(false);
    setSearchOutcome(null);
    setAnswerOpen(false);
    setAnswerOutcome(null);
    setConversationView(null);
    setWorkflowView(null);
    setWorkflowHistoryView(null);
    try {
      const restored = await restoreSession(controller.signal);
      if (!isCurrent(id, controller)) return;
      if (!restored) { reset(); return; }
      setSession(restored);
      setPhase('workspace');
      await refresh();
    } catch (failure) {
      if (isCurrent(id, controller)) reset(
        failure instanceof ApiError ? failure.message : '无法确认登录状态，请稍后重试。', 'restore-error',
      );
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function openWorkspace(workspace: Workspace) {
    const { id, controller } = beginRequest();
    setSelectedWorkspace(workspace);
    setKnowledgeBases([]);
    setSelectedKnowledgeBase(null);
    setDocuments([]);
    setUploadOutcome(null);
    setSelectedDocument(null);
    setDocumentDetail(null);
    setChunks(null);
    setChunkOutcome(null);
    setEmbeddingOutcome(null);
    setSearchOpen(false);
    setSearchOutcome(null);
    setAnswerOpen(false);
    setAnswerOutcome(null);
    setConversationView(null);
    setWorkflowView(null);
    setWorkflowHistoryView(null);
    setBusy('knowledge-bases');
    setError('');
    try {
      const rows = await listKnowledgeBases(workspace.id, controller.signal);
      if (isCurrent(id, controller)) setKnowledgeBases(rows);
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else if (failure instanceof ApiError && failure.status === 404) {
        setError('该工作空间不存在或你已无权访问，请返回工作空间列表。');
      } else setError(failure instanceof ApiError ? failure.message : '无法读取知识库，请稍后重试。');
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function backToWorkspaces() {
    setSelectedWorkspace(null);
    setKnowledgeBases([]);
    setSelectedKnowledgeBase(null);
    setDocuments([]);
    setUploadOutcome(null);
    setSelectedDocument(null);
    setDocumentDetail(null);
    setChunks(null);
    setChunkOutcome(null);
    setEmbeddingOutcome(null);
    setSearchOpen(false);
    setSearchOutcome(null);
    setAnswerOpen(false);
    setAnswerOutcome(null);
    setConversationView(null);
    setWorkflowView(null);
    setWorkflowHistoryView(null);
    void refresh();
  }

  async function openKnowledgeBase(knowledgeBase: KnowledgeBase) {
    if (!selectedWorkspace) return;
    const { id, controller } = beginRequest();
    setSelectedKnowledgeBase(knowledgeBase);
    setDocuments([]);
    setSelectedDocument(null);
    setDocumentDetail(null);
    setChunks(null);
    setChunkOutcome(null);
    setEmbeddingOutcome(null);
    setSearchOpen(false);
    setSearchOutcome(null);
    setAnswerOpen(false);
    setAnswerOutcome(null);
    setConversationView(null);
    setWorkflowView(null);
    setWorkflowHistoryView(null);
    setBusy('documents');
    setError('');
    try {
      const rows = await listDocuments(selectedWorkspace.id, knowledgeBase.id, controller.signal);
      if (isCurrent(id, controller)) setDocuments(rows);
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else if (failure instanceof ApiError && failure.status === 404) {
        setError('该知识库不存在或你已无权访问，请返回知识库列表。');
      } else setError(failure instanceof ApiError ? failure.message : '无法读取文档，请稍后重试。');
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function backToKnowledgeBases() {
    if (selectedWorkspace) void openWorkspace(selectedWorkspace);
  }

  async function readDocument(documentId: string, preserveOutcome = true) {
    if (!selectedWorkspace || !selectedKnowledgeBase) return;
    const { id, controller } = beginRequest();
    setDocumentDetail(null);
    setChunks(null);
    if (!preserveOutcome) {
      setChunkOutcome(null);
      setEmbeddingOutcome(null);
    }
    setBusy('document-detail');
    setError('');
    try {
      const detail = await getDocument(selectedWorkspace.id, selectedKnowledgeBase.id, documentId, controller.signal);
      if (!isCurrent(id, controller)) return;
      setDocumentDetail(detail);
      const rows = await listDocumentChunks(selectedWorkspace.id, selectedKnowledgeBase.id, documentId, controller.signal);
      if (isCurrent(id, controller)) {
        setChunks(rows);
        setChunkOutcome(current => current?.kind === 'confirmed' ? current : null);
        setEmbeddingOutcome(current => current?.kind === 'confirmed' ? current : null);
      }
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else if (failure instanceof ApiError && failure.status === 404) {
        setDocumentDetail(null);
        setError('该文档不存在或你已无权访问，请返回文档列表。');
      } else setError(failure instanceof ApiError ? failure.message : '无法读取文档与切块记录，请稍后重试。');
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function openDocument(document: KnowledgeDocument) {
    if (pending.current) return;
    setSelectedDocument(document);
    void readDocument(document.id, false);
  }

  function backToDocuments() {
    if (selectedKnowledgeBase) void openKnowledgeBase(selectedKnowledgeBase);
  }

  function openSearch() {
    if (!selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null || error) return;
    setSearchOpen(true);
    setSearchOutcome(null);
    setAnswerOpen(false);
    setAnswerOutcome(null);
    setConversationView(null);
    setWorkflowView(null);
    setWorkflowHistoryView(null);
    setUploadOutcome(null);
    setError('');
  }

  async function search(query: string) {
    if (!searchOpen || !selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null) return;
    const workspaceId = selectedWorkspace.id;
    const knowledgeBaseId = selectedKnowledgeBase.id;
    const submittedQuery = query.trim();
    const { id, controller } = beginRequest();
    setBusy('search');
    setError('');
    setSearchOutcome(null);
    try {
      const result = await searchKnowledgeBase(workspaceId, knowledgeBaseId, submittedQuery, controller.signal);
      if (isCurrent(id, controller)) setSearchOutcome({ kind: 'confirmed', query: submittedQuery, result });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setSearchOutcome(failure instanceof SearchUnconfirmedError || !(failure instanceof ApiError)
        ? { kind: 'unconfirmed', message: SEARCH_UNCONFIRMED_MESSAGE }
        : { kind: 'rejected', message: failure.message });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function stopWaitingForSearch() {
    if (busy !== 'search') return;
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    setBusy(null);
    setSearchOutcome({ kind: 'unconfirmed', message: SEARCH_UNCONFIRMED_MESSAGE });
  }

  function openAnswer() {
    if (!selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null || error) return;
    setAnswerOpen(true);
    setAnswerOutcome(null);
    setSearchOpen(false);
    setSearchOutcome(null);
    setConversationView(null);
    setWorkflowView(null);
    setWorkflowHistoryView(null);
    setUploadOutcome(null);
    setError('');
  }

  async function answer(question: string) {
    if (!answerOpen || !selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null) return;
    const workspaceId = selectedWorkspace.id;
    const knowledgeBaseId = selectedKnowledgeBase.id;
    const submittedQuestion = question.trim();
    const { id, controller } = beginRequest();
    setBusy('answer');
    setError('');
    setAnswerOutcome(null);
    try {
      const result = await answerKnowledgeBase(workspaceId, knowledgeBaseId, submittedQuestion, controller.signal);
      if (isCurrent(id, controller)) setAnswerOutcome({ kind: 'confirmed', question: submittedQuestion, result });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setAnswerOutcome(failure instanceof AnswerUnconfirmedError || !(failure instanceof ApiError)
        ? { kind: 'unconfirmed', message: ANSWER_UNCONFIRMED_MESSAGE,
          traceId: failure instanceof ApiError ? failure.traceId : null }
        : { kind: 'rejected', message: failure.message, traceId: failure.traceId });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function stopWaitingForAnswer() {
    if (busy !== 'answer') return;
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    setBusy(null);
    setAnswerOutcome({ kind: 'unconfirmed', message: ANSWER_UNCONFIRMED_MESSAGE });
  }

  function openConversation() {
    if (!selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null || error) return;
    setWorkflowView(null);
    setWorkflowHistoryView(null);
    setConversationView({ createAttempted: false, created: null, createOutcome: null,
      request: null, messageOutcome: null, snapshot: null, readError: '',
      conditions: null, conditionRequest: null, conditionNotice: '',
      executionRequest: null, executionResult: null, executionNotice: '' });
    setSearchOpen(false);
    setSearchOutcome(null);
    setAnswerOpen(false);
    setAnswerOutcome(null);
    setUploadOutcome(null);
    setError('');
  }

  async function createBusinessConversation() {
    if (!conversationView || conversationView.createAttempted || !selectedWorkspace || !selectedKnowledgeBase
        || pending.current || busy !== null) return;
    const workspaceId = selectedWorkspace.id;
    const knowledgeBaseId = selectedKnowledgeBase.id;
    const { id, controller } = beginRequest();
    setBusy('conversation-create');
    setError('');
    setConversationView(view => view && { ...view, createAttempted: true, createOutcome: null });
    try {
      const created = await createConversation(workspaceId, knowledgeBaseId, controller.signal);
      if (isCurrent(id, controller)) setConversationView(view => view && { ...view, created });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setConversationView(view => view && { ...view, createOutcome:
        failure instanceof ConversationUnconfirmedError || !(failure instanceof ApiError)
          ? { kind: 'unconfirmed', message: CONVERSATION_CREATE_UNCONFIRMED_MESSAGE }
          : { kind: 'rejected', message: failure.message } });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function sendFirstMessage(content: string) {
    if (!conversationView?.created || conversationView.request || !selectedWorkspace || !selectedKnowledgeBase
        || pending.current || busy !== null) return;
    const workspaceId = selectedWorkspace.id;
    const knowledgeBaseId = selectedKnowledgeBase.id;
    const conversationId = conversationView.created.id;
    const first: FirstMessageRequest = { requestId: crypto.randomUUID(), expectedVersion: 0, content: content.trim() };
    const { id, controller } = beginRequest();
    setBusy('conversation-message');
    setError('');
    setConversationView(view => view && { ...view, request: first, messageOutcome: null, snapshot: null, readError: '' });
    try {
      const result = await sendFirstConversationMessage(workspaceId, knowledgeBaseId, conversationId, first, controller.signal);
      if (isCurrent(id, controller)) setConversationView(view => view && { ...view, messageOutcome: { kind: 'confirmed', result },
        conditions: result.pending ? { version: 1, status: 'waiting_for_user', pending: result.pending, inputs: [] } : null });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setConversationView(view => view && { ...view, messageOutcome:
        failure instanceof ConversationUnconfirmedError || !(failure instanceof ApiError)
          ? { kind: 'unconfirmed', message: CONVERSATION_MESSAGE_UNCONFIRMED_MESSAGE }
          : { kind: 'rejected', message: failure.message } });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function supplementConversation(value: string) {
    if (!conversationView?.created || !conversationView.conditions || conversationView.conditionRequest
        || conversationView.conditionNotice || conversationView.conditions.status !== 'waiting_for_user'
        || !selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null) return;
    const previous = conversationView.conditions;
    const conversationId = conversationView.created.id;
    const submitted: ConditionRequest = { requestId: crypto.randomUUID(), expectedVersion: previous.version, value };
    const { id, controller } = beginRequest();
    setBusy('conversation-supplement');
    setConversationView(view => view && { ...view, conditionRequest: { ...submitted,
      label: previous.pending.current.label, unit: previous.pending.current.unit }, snapshot: null, readError: '' });
    try {
      const conditions = await submitConversationCondition(selectedWorkspace.id, selectedKnowledgeBase.id,
        conversationId, previous, submitted, controller.signal);
      if (isCurrent(id, controller)) setConversationView(view => view && { ...view, conditions, conditionRequest: null });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setConversationView(view => view && { ...view, conditionNotice:
        failure instanceof ConversationUnconfirmedError || !(failure instanceof ApiError)
          ? CONDITION_UNCONFIRMED_MESSAGE : `${failure.message} 后续补充已暂停。` });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function readCurrentConversation() {
    if (!conversationView?.created || !conversationView.request || !selectedWorkspace || !selectedKnowledgeBase
        || pending.current || busy !== null) return;
    const workspaceId = selectedWorkspace.id;
    const knowledgeBaseId = selectedKnowledgeBase.id;
    const conversationId = conversationView.created.id;
    const { id, controller } = beginRequest();
    setBusy('conversation-read');
    setError('');
    setConversationView(view => view && { ...view, snapshot: null, readError: '' });
    try {
      const snapshot = await getConversationSnapshot(workspaceId, knowledgeBaseId, conversationId, controller.signal);
      if (isCurrent(id, controller)) setConversationView(view => {
        if (!view) return view;
        const changed = view.conditions && (snapshot.version !== (view.executionResult?.version ?? view.conditions.version)
          || snapshot.status !== (view.executionResult ? 'completed' : view.conditions.status));
        return { ...view, snapshot,
          executionResult: changed ? null : view.executionResult,
          conditionNotice: changed ? '会话记录已变化，后续操作已暂停；读取状态不会恢复可提交的任务。' : view.conditionNotice };
      });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else if (failure instanceof ApiError && failure.status === 404) setConversationView(view => view && {
        ...view, snapshot: null, messageOutcome: null, conditions: null, executionResult: null, readError: failure.message,
        conditionNotice: view.conditions || view.conditionRequest ? '会话不可访问，后续补充已暂停。' : '',
      });
      else setConversationView(view => view && { ...view, readError:
        failure instanceof ApiError && !(failure instanceof ConversationUnconfirmedError) ? failure.message
          : '当前会话状态未确认，请按需重新读取；不会重发消息。' });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function markConversationInterrupted(activity: Busy) {
    if (activity === 'conversation-create') setConversationView(view => view && { ...view,
      createOutcome: { kind: 'unconfirmed', message: CONVERSATION_CREATE_UNCONFIRMED_MESSAGE } });
    if (activity === 'conversation-message') setConversationView(view => view && { ...view,
      messageOutcome: { kind: 'unconfirmed', message: CONVERSATION_MESSAGE_UNCONFIRMED_MESSAGE } });
    if (activity === 'conversation-supplement') setConversationView(view => view && { ...view,
      conditionNotice: CONDITION_UNCONFIRMED_MESSAGE });
    if (activity === 'conversation-execute') setConversationView(view => view && { ...view,
      executionNotice: BILLING_UNCONFIRMED_MESSAGE });
    if (activity === 'conversation-read') setConversationView(view => view && { ...view,
      readError: '当前会话状态未确认，请按需重新读取；不会重发消息。' });
  }

  function stopWaitingForConversation() {
    if (busy !== 'conversation-create' && busy !== 'conversation-message' && busy !== 'conversation-read'
        && busy !== 'conversation-supplement' && busy !== 'conversation-execute') return;
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    markConversationInterrupted(busy);
    setBusy(null);
  }

  async function executeBilling() {
    if (!conversationView?.created || !conversationView.conditions || conversationView.executionRequest
        || conversationView.conditionRequest || conversationView.conditionNotice || conversationView.executionNotice
        || conversationView.conditions.status !== 'ready' || conversationView.conditions.pending.kind !== 'billing'
        || !selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null) return;
    const previous = conversationView.conditions;
    const submitted: BillingExecutionRequest = {
      requestId: crypto.randomUUID(), expectedVersion: previous.version, confirmed: true,
    };
    const { id, controller } = beginRequest();
    setBusy('conversation-execute');
    setConversationView(view => view && { ...view, executionRequest: submitted, snapshot: null, readError: '' });
    try {
      const executionResult = await executeConversationBilling(selectedWorkspace.id, selectedKnowledgeBase.id,
        conversationView.created.id, previous, submitted, controller.signal);
      if (isCurrent(id, controller)) setConversationView(view => view && { ...view, executionResult });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setConversationView(view => view && { ...view, executionNotice:
        failure instanceof ConversationUnconfirmedError || !(failure instanceof ApiError)
          ? BILLING_UNCONFIRMED_MESSAGE : `${failure.message} 本页不会再次执行。` });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function openWorkflowHistory() {
    if (!selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null || error) return;
    setSearchOpen(false); setSearchOutcome(null); setAnswerOpen(false); setAnswerOutcome(null);
    setConversationView(null); setWorkflowView(null); setUploadOutcome(null); setError('');
    setWorkflowHistoryView({ items: null, listNotice: null, selected: null, snapshot: null, readNotice: null,
      savedAnswer: null, savedAnswerNotice: '' });
  }

  async function listWorkflowHistory() {
    if (!workflowHistoryView || !selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null) return;
    const { id, controller } = beginRequest();
    setBusy('workflow-history-list'); setError('');
    setWorkflowHistoryView({ items: null, listNotice: null, selected: null, snapshot: null, readNotice: null,
      savedAnswer: null, savedAnswerNotice: '' });
    try {
      const items = await listAnswerWorkflows(selectedWorkspace.id, selectedKnowledgeBase.id, controller.signal);
      if (isCurrent(id, controller)) setWorkflowHistoryView(view => view && { ...view, items });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setWorkflowHistoryView(view => view && { ...view, listNotice: {
        message: failure instanceof ApiError ? failure.message : '工作流记录读取未确认，请按需重新读取。',
        traceId: failure instanceof ApiError ? failure.traceId : null } });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function readHistoryWorkflow(item: WorkflowHistoryItem) {
    if (!workflowHistoryView?.items?.includes(item) || item.strategy !== 'vector'
        || !selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null) return;
    const { id, controller } = beginRequest();
    setBusy('workflow-history-read'); setError('');
    setWorkflowHistoryView(view => view && { ...view, selected: item, snapshot: null, readNotice: null,
      savedAnswer: null, savedAnswerNotice: '' });
    try {
      const snapshot = await readAnswerWorkflow(selectedWorkspace.id, selectedKnowledgeBase.id, item, controller.signal);
      if (isCurrent(id, controller)) setWorkflowHistoryView(view => view && { ...view, snapshot });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setWorkflowHistoryView(view => view && { ...view, readNotice: {
        message: failure instanceof ApiError && !(failure instanceof WorkflowUnconfirmedError) ? failure.message : WORKFLOW_NOTICES.read,
        traceId: failure instanceof ApiError ? failure.traceId : null } });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function readHistoryAnswer() {
    if (!workflowHistoryView?.selected || !workflowHistoryView.snapshot
        || !canReadSavedWorkflowAnswer(workflowHistoryView.snapshot)
        || !selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null) return;
    const selected = workflowHistoryView.selected, snapshot = workflowHistoryView.snapshot;
    const { id, controller } = beginRequest();
    setBusy('workflow-history-answer'); setError('');
    setWorkflowHistoryView(view => view && { ...view, savedAnswer: null, savedAnswerNotice: '' });
    try {
      const savedAnswer = await readSavedWorkflowAnswer(selectedWorkspace.id, selectedKnowledgeBase.id,
        selected, snapshot, controller.signal);
      if (isCurrent(id, controller)) setWorkflowHistoryView(view => view && { ...view, savedAnswer });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setWorkflowHistoryView(view => view && { ...view, snapshot: null, savedAnswer: null, savedAnswerNotice:
        failure instanceof ApiError && !(failure instanceof WorkflowUnconfirmedError)
          ? failure.message : WORKFLOW_NOTICES['answer-read'] });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function markHistoryInterrupted(activity: Busy) {
    if (activity !== 'workflow-history-list' && activity !== 'workflow-history-read' && activity !== 'workflow-history-answer') return;
    setWorkflowHistoryView(view => view && { ...view, snapshot: null, savedAnswer: null,
      listNotice: activity === 'workflow-history-list' ? { message: '工作流记录读取已停止，请按需重新读取。', traceId: null } : view.listNotice,
      readNotice: activity === 'workflow-history-read' ? { message: WORKFLOW_NOTICES.read, traceId: null } : view.readNotice,
      savedAnswerNotice: activity === 'workflow-history-answer' ? WORKFLOW_NOTICES['answer-read'] : view.savedAnswerNotice });
  }

  function stopWaitingForHistory() {
    if (busy !== 'workflow-history-list' && busy !== 'workflow-history-read' && busy !== 'workflow-history-answer') return;
    generation.current += 1; pending.current?.abort(); pending.current = null;
    markHistoryInterrupted(busy); setBusy(null);
  }

  function openWorkflow() {
    if (!selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null || error) return;
    setSearchOpen(false); setSearchOutcome(null); setAnswerOpen(false); setAnswerOutcome(null);
    setConversationView(null); setWorkflowHistoryView(null); setUploadOutcome(null); setError('');
    setWorkflowView({ creation: null, created: null, createNotice: '', execution: null, receipt: null,
      executeNotice: '', snapshot: null, readNotice: '', blocked: false,
      resumeRequest: null, resumeReceipt: null, resumeNotice: '', savedAnswer: null, savedAnswerNotice: '' });
  }

  async function createWorkflow(question: string) {
    if (!workflowView || workflowView.creation || !selectedWorkspace || !selectedKnowledgeBase
        || pending.current || busy !== null) return;
    const creation: WorkflowCreation = { requestId: crypto.randomUUID(), question: question.trim() };
    const { id, controller } = beginRequest();
    setBusy('workflow-create'); setError('');
    setWorkflowView(view => view && { ...view, creation, createNotice: '' });
    try {
      const created = await createAnswerWorkflow(selectedWorkspace.id, selectedKnowledgeBase.id, creation, controller.signal);
      if (isCurrent(id, controller)) setWorkflowView(view => view && { ...view, created });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setWorkflowView(view => view && { ...view, blocked: true, createNotice:
        failure instanceof ApiError && !(failure instanceof WorkflowUnconfirmedError) ? failure.message : WORKFLOW_NOTICES.create });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function executeWorkflow() {
    if (!workflowView?.created || workflowView.execution || workflowView.blocked || !selectedWorkspace || !selectedKnowledgeBase
        || pending.current || busy !== null) return;
    const created = workflowView.created;
    const execution: WorkflowExecution = { requestId: crypto.randomUUID(), expectedVersion: 0 };
    const { id, controller } = beginRequest();
    setBusy('workflow-execute'); setError('');
    setWorkflowView(view => view && { ...view, execution, receipt: null, executeNotice: '', snapshot: null, readNotice: '',
      savedAnswer: null, savedAnswerNotice: '' });
    try {
      const receipt = await executeAnswerWorkflow(selectedWorkspace.id, selectedKnowledgeBase.id, created, execution, controller.signal);
      if (isCurrent(id, controller)) setWorkflowView(view => view && { ...view, receipt, blocked: true });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setWorkflowView(view => view && { ...view, blocked: true, executeNotice:
        failure instanceof ApiError && !(failure instanceof WorkflowUnconfirmedError) ? failure.message : WORKFLOW_NOTICES.execute });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function readWorkflow() {
    if (!workflowView?.created || !selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null) return;
    const created = workflowView.created;
    const { id, controller } = beginRequest();
    setBusy('workflow-read'); setError('');
    setWorkflowView(view => view && { ...view, snapshot: null, readNotice: '', savedAnswer: null, savedAnswerNotice: '' });
    try {
      const snapshot = await readAnswerWorkflow(selectedWorkspace.id, selectedKnowledgeBase.id, created, controller.signal);
      if (isCurrent(id, controller)) setWorkflowView(view => {
        if (!view) return view;
        const previous = view.resumeReceipt?.workflow ?? view.receipt?.workflow ?? created;
        const checkpointMismatch = !view.resumeRequest && view.receipt?.status === 'failed'
          && previous.nextStep === 'generate' && previous.retryable && sameWorkflowMeta(snapshot, previous)
          && !canResumeWorkflow(view.receipt, snapshot);
        const changed = !sameWorkflowMeta(snapshot, previous) || checkpointMismatch;
        return { ...view, snapshot, blocked: view.blocked || changed,
          receipt: changed ? null : view.receipt, resumeReceipt: changed ? null : view.resumeReceipt,
          readNotice: changed ? '当前任务记录已变化，本页暂停写入；读取状态不会确认本次执行回执或解锁重发。' : '' };
      });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setWorkflowView(view => view && { ...view,
        receipt: failure instanceof ApiError && failure.status === 404 ? null : view.receipt,
        resumeReceipt: failure instanceof ApiError && failure.status === 404 ? null : view.resumeReceipt,
        blocked: view.blocked || (failure instanceof ApiError && failure.status === 404), readNotice:
          failure instanceof ApiError && !(failure instanceof WorkflowUnconfirmedError) ? failure.message : WORKFLOW_NOTICES.read });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function readWorkflowAnswer() {
    if (!workflowView?.created || !workflowView.snapshot || !canReadSavedWorkflowAnswer(workflowView.snapshot)
        || !selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null) return;
    const created = workflowView.created, snapshot = workflowView.snapshot;
    const { id, controller } = beginRequest();
    setBusy('workflow-answer-read'); setError('');
    // A fresh source check replaces previously displayed bodies as one unit.
    setWorkflowView(view => view && { ...view, savedAnswer: null, savedAnswerNotice: '',
      receipt: null, resumeReceipt: null, blocked: true });
    try {
      const savedAnswer = await readSavedWorkflowAnswer(selectedWorkspace.id, selectedKnowledgeBase.id,
        created, snapshot, controller.signal);
      if (isCurrent(id, controller)) setWorkflowView(view => view && { ...view, savedAnswer });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setWorkflowView(view => view && { ...view, snapshot: null, savedAnswer: null, savedAnswerNotice:
        failure instanceof ApiError && !(failure instanceof WorkflowUnconfirmedError)
          ? failure.message : WORKFLOW_NOTICES['answer-read'] });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function resumeWorkflow() {
    if (!workflowView?.created || !workflowView.receipt || !workflowView.snapshot || workflowView.resumeRequest
        || workflowView.readNotice || !canResumeWorkflow(workflowView.receipt, workflowView.snapshot)
        || !selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null) return;
    const failure = workflowView.receipt, current = workflowView.snapshot;
    const submitted: WorkflowResume = { requestId: crypto.randomUUID(), expectedVersion: current.version, confirmed: true };
    const { id, controller } = beginRequest();
    setBusy('workflow-resume'); setError('');
    setWorkflowView(view => view && { ...view, resumeRequest: submitted, resumeReceipt: null,
      resumeNotice: '', snapshot: null, readNotice: '', savedAnswer: null, savedAnswerNotice: '' });
    try {
      const resumeReceipt = await resumeAnswerWorkflow(selectedWorkspace.id, selectedKnowledgeBase.id,
        failure, current, submitted, controller.signal);
      if (isCurrent(id, controller)) setWorkflowView(view => view && { ...view, resumeReceipt });
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else setWorkflowView(view => view && { ...view, resumeNotice:
        failure instanceof ApiError && !(failure instanceof WorkflowUnconfirmedError) ? failure.message : WORKFLOW_NOTICES.resume });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function markWorkflowInterrupted(activity: Busy) {
    const stage = activity === 'workflow-create' ? 'create' : activity === 'workflow-execute' ? 'execute'
      : activity === 'workflow-resume' ? 'resume' : activity === 'workflow-read' ? 'read'
        : activity === 'workflow-answer-read' ? 'answer-read' : null;
    if (!stage) return;
    setWorkflowView(view => view && { ...view, blocked: view.blocked || (stage !== 'read' && stage !== 'answer-read'),
      createNotice: stage === 'create' ? WORKFLOW_NOTICES.create : view.createNotice,
      executeNotice: stage === 'execute' ? WORKFLOW_NOTICES.execute : view.executeNotice,
      resumeNotice: stage === 'resume' ? WORKFLOW_NOTICES.resume : view.resumeNotice,
      readNotice: stage === 'read' ? WORKFLOW_NOTICES.read : view.readNotice,
      savedAnswer: stage === 'answer-read' ? null : view.savedAnswer,
      snapshot: stage === 'answer-read' ? null : view.snapshot,
      savedAnswerNotice: stage === 'answer-read' ? WORKFLOW_NOTICES['answer-read'] : view.savedAnswerNotice });
  }

  function stopWaitingForWorkflow() {
    if (busy !== 'workflow-create' && busy !== 'workflow-execute' && busy !== 'workflow-read'
        && busy !== 'workflow-resume' && busy !== 'workflow-answer-read') return;
    generation.current += 1; pending.current?.abort(); pending.current = null;
    markWorkflowInterrupted(busy); setBusy(null);
  }

  async function chunkDocument() {
    if (!selectedWorkspace || !selectedKnowledgeBase || !selectedDocument || !documentDetail
        || chunks === null || error || pending.current || busy !== null
        || (documentDetail.status !== 'parsed' && documentDetail.status !== 'ready')) return;
    const workspaceId = selectedWorkspace.id;
    const knowledgeBaseId = selectedKnowledgeBase.id;
    const documentId = selectedDocument.id;
    const { id, controller } = beginRequest();
    setBusy('chunking');
    setChunkOutcome(null);
    setChunks(null);
    try {
      const rows = await createDocumentChunks(workspaceId, knowledgeBaseId, documentId, controller.signal);
      if (!isCurrent(id, controller)) return;
      setChunks(rows);
      setChunkOutcome({ kind: 'confirmed', count: rows.length });
      // 切块结果与最新文档状态分别确认，读取故障不撤销已确认的切块响应。
      setDocumentDetail(null);
      setBusy('document-detail');
      try {
        const detail = await getDocument(workspaceId, knowledgeBaseId, documentId, controller.signal);
        if (isCurrent(id, controller)) setDocumentDetail(detail);
      } catch (failure) {
        if (!isCurrent(id, controller)) return;
        if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
        else {
          if (failure instanceof ApiError && failure.status === 404) setChunks(null);
          setError('切块响应已确认，但文档最新状态未确认。' + (failure instanceof ApiError
            ? failure.message : '请刷新后核对。'));
        }
      }
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else {
        setDocumentDetail(null);
        setChunkOutcome(failure instanceof ChunkUnconfirmedError || !(failure instanceof ApiError)
          ? { kind: 'unconfirmed', message: CHUNK_UNCONFIRMED_MESSAGE }
          : { kind: 'rejected', message: failure.message });
      }
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function stopWaitingForChunking() {
    if (busy !== 'chunking') return;
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    setBusy(null);
    setDocumentDetail(null);
    setChunks(null);
    setChunkOutcome({ kind: 'unconfirmed', message: CHUNK_UNCONFIRMED_MESSAGE });
  }

  async function embedDocument(maxChunks: number) {
    if (!selectedWorkspace || !selectedKnowledgeBase || !selectedDocument || !documentDetail
        || chunks === null || chunks.length === 0 || error || pending.current || busy !== null
        || (documentDetail.status !== 'parsed' && documentDetail.status !== 'ready')
        || !Number.isSafeInteger(maxChunks) || maxChunks < 1 || maxChunks > 1000
        || (documentDetail.status === 'parsed' && chunks.length > maxChunks)) return;
    const workspaceId = selectedWorkspace.id;
    const knowledgeBaseId = selectedKnowledgeBase.id;
    const documentId = selectedDocument.id;
    const expectedChunkCount = chunks.length;
    const { id, controller } = beginRequest();
    setBusy('embedding');
    setEmbeddingOutcome(null);
    try {
      const result = await createDocumentEmbeddings(workspaceId, knowledgeBaseId, documentId, maxChunks, controller.signal);
      if (!isCurrent(id, controller)) return;
      if (result.chunkCount !== expectedChunkCount) throw new EmbeddingUnconfirmedError();
      setEmbeddingOutcome({ kind: 'confirmed', result });
      setDocumentDetail(null);
      setBusy('document-detail');
      try {
        const detail = await getDocument(workspaceId, knowledgeBaseId, documentId, controller.signal);
        if (isCurrent(id, controller)) setDocumentDetail(detail);
      } catch (failure) {
        if (!isCurrent(id, controller)) return;
        if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
        else {
          if (failure instanceof ApiError && failure.status === 404) setChunks(null);
          setError('向量化成功响应已校验，但文档最新状态未确认。' + (failure instanceof ApiError
            ? failure.message : '请刷新后核对。'));
        }
      }
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else {
        setDocumentDetail(null);
        setChunks(null);
        setEmbeddingOutcome(failure instanceof EmbeddingUnconfirmedError || !(failure instanceof ApiError)
          ? { kind: 'unconfirmed', message: EMBEDDING_UNCONFIRMED_MESSAGE }
          : { kind: 'rejected', message: failure.message });
      }
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function stopWaitingForEmbedding() {
    if (busy !== 'embedding') return;
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    setBusy(null);
    setDocumentDetail(null);
    setChunks(null);
    setEmbeddingOutcome({ kind: 'unconfirmed', message: EMBEDDING_UNCONFIRMED_MESSAGE });
  }

  async function upload(file: File) {
    if (!selectedWorkspace || !selectedKnowledgeBase || pending.current || busy !== null) return;
    const workspace = selectedWorkspace;
    const knowledgeBase = selectedKnowledgeBase;
    const { id, controller } = beginRequest();
    setBusy('upload');
    setUploadOutcome(null);
    try {
      const document = await uploadDocument(workspace.id, knowledgeBase.id, file, controller.signal);
      if (!isCurrent(id, controller)) return;
      setUploadOutcome({ kind: 'confirmed', document });
      // 列表只代表第一页；刷新失败也不能抹去已确认的本次上传结果。
      await openKnowledgeBase(knowledgeBase);
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) reset(failure.message);
      else if (failure instanceof UploadUnconfirmedError) {
        setUploadOutcome({ kind: 'unconfirmed', message: failure.message });
      } else if (failure instanceof ApiError) {
        setUploadOutcome({ kind: 'rejected', message: failure.message });
      } else setUploadOutcome({ kind: 'unconfirmed', message: UPLOAD_UNCONFIRMED_MESSAGE });
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  function stopWaitingForUpload() {
    if (busy !== 'upload') return;
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    setBusy(null);
    setUploadOutcome({ kind: 'unconfirmed', message: UPLOAD_UNCONFIRMED_MESSAGE });
  }

  async function exit() {
    setWorkflowHistoryView(view => view && { ...view,
      listNotice: view.listNotice && { ...view.listNotice, traceId: null },
      readNotice: view.readNotice && { ...view.readNotice, traceId: null } });
    if (answerOpen) {
      setAnswerOutcome(outcome => outcome && outcome.kind !== 'confirmed'
        ? { kind: outcome.kind, message: outcome.message } : outcome);
    }
    markConversationInterrupted(busy);
    markWorkflowInterrupted(busy);
    markHistoryInterrupted(busy);
    if (busy === 'answer') {
      setAnswerOutcome({ kind: 'unconfirmed', message: ANSWER_UNCONFIRMED_MESSAGE });
    }
    if (busy === 'search') {
      setSearchOutcome({ kind: 'unconfirmed', message: SEARCH_UNCONFIRMED_MESSAGE });
    }
    if (busy === 'upload') {
      setUploadOutcome({ kind: 'unconfirmed', message: UPLOAD_UNCONFIRMED_MESSAGE });
    }
    if (busy === 'chunking') {
      setDocumentDetail(null);
      setChunks(null);
      setChunkOutcome({ kind: 'unconfirmed', message: CHUNK_UNCONFIRMED_MESSAGE });
    }
    if (busy === 'embedding') {
      setDocumentDetail(null);
      setChunks(null);
      setEmbeddingOutcome({ kind: 'unconfirmed', message: EMBEDDING_UNCONFIRMED_MESSAGE });
    }
    const { id, controller } = beginRequest();
    setBusy('logout');
    setError('');
    try {
      await logout(controller.signal);
      if (isCurrent(id, controller)) reset();
    } catch (failure) {
      if (isCurrent(id, controller)) setError('退出尚未确认。' + (
        failure instanceof ApiError ? failure.message : '请重试退出。'
      ));
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current) return;
    const submittedPassword = password;
    setPassword('');
    const { id, controller } = beginRequest();
    setBusy('login');
    setError('');
    try {
      const authenticated = await login(username, submittedPassword, controller.signal);
      if (!isCurrent(id, controller)) return;
      setSession(authenticated);
      setPhase('workspace');
      await refresh();
    } catch (failure) {
      if (isCurrent(id, controller)) reset(
        failure instanceof ApiError ? failure.message : '无法完成登录，请稍后重试。',
      );
    } finally {
      if (isCurrent(id, controller)) { setBusy(null); pending.current = null; }
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">EA</span>
          <span>企业知识与工作流智能体平台</span>
        </div>
        {session && <div className="account">
          <span>当前账号：{session.user.username}</span>
          <button className="button secondary" disabled={busy === 'logout'} onClick={() => void exit()}>
            {busy === 'logout' ? '正在退出…' : '退出登录'}
          </button>
        </div>}
      </header>

      <main className={session ? 'workspace-page' : 'login-page'}>
        {phase === 'restoring' ? <section className="login-card" aria-labelledby="restore-title">
          <h1 id="restore-title">正在恢复登录</h1>
          <p className="intro" role="status">正在向后端核实当前登录状态…</p>
        </section> : phase === 'restore-error' ? <section className="login-card" aria-labelledby="restore-error-title">
          <h1 id="restore-error-title">暂时无法恢复登录</h1>
          <p className="message error" role="alert">{error}</p>
          <p className="intro">请检查后端连接后重试，或清除本浏览器登录后返回登录页。</p>
          <div className="recovery-actions">
            <button className="button primary" disabled={busy === 'logout'} onClick={() => void restore()}>重试恢复</button>
            <button className="button secondary" disabled={busy === 'logout'} onClick={() => void exit()}>
              {busy === 'logout' ? '正在退出…' : '返回登录'}
            </button>
          </div>
        </section> : !session ? <section className="login-card" aria-labelledby="login-title">
          <p className="eyebrow">欢迎回来</p>
          <h1 id="login-title">登录工作空间</h1>
          <p className="intro">使用已有账号，查看你可以访问的企业工作空间。</p>
          {error && <p className="message error" role="alert">{error}</p>}
          <form onSubmit={submit} aria-busy={busy === 'login'}>
            <label htmlFor="username">用户名</label>
            <input id="username" name="username" autoComplete="username" required maxLength={32}
              value={username} onChange={event => setUsername(event.target.value)} disabled={busy === 'login'} />
            <label htmlFor="password">密码</label>
            <input id="password" name="password" type="password" autoComplete="current-password"
              required maxLength={256} value={password} onChange={event => setPassword(event.target.value)}
              disabled={busy === 'login'} />
            <button className="button primary" type="submit" disabled={busy === 'login'}>
              {busy === 'login' ? '正在登录…' : '登录'}
            </button>
            {busy === 'login' && <button className="button secondary" type="button"
              onClick={() => reset('已停止等待登录结果。登录可能已在后端生效，可尝试恢复登录。')}>取消登录</button>}
          </form>
          {error && busy !== 'login' && <button className="button secondary" onClick={() => void restore()}>恢复已有登录</button>}
          <p className="session-note">登录后最多保持 30 分钟，刷新或重新打开网页不会延长有效期。请在共用设备上主动退出。</p>
        </section> : selectedWorkspace && selectedKnowledgeBase && workflowHistoryView ? <WorkflowHistory
          workspaceName={selectedWorkspace.name} knowledgeBaseName={selectedKnowledgeBase.name}
          activity={busy === 'workflow-history-list' ? 'listing' : busy === 'workflow-history-read' ? 'reading'
            : busy === 'workflow-history-answer' ? 'answer-reading' : busy === 'logout' ? 'logout' : 'idle'}
          view={workflowHistoryView} error={error} onList={() => void listWorkflowHistory()}
          onSelect={item => void readHistoryWorkflow(item)}
          onRead={() => { if (workflowHistoryView.selected) void readHistoryWorkflow(workflowHistoryView.selected); }}
          onReadAnswer={() => void readHistoryAnswer()} onCancel={stopWaitingForHistory} onBack={backToDocuments}
        /> : selectedWorkspace && selectedKnowledgeBase && workflowView ? <KnowledgeWorkflow
          key={`${selectedWorkspace.id}/${selectedKnowledgeBase.id}`}
          workspaceName={selectedWorkspace.name} knowledgeBaseName={selectedKnowledgeBase.name}
          activity={busy === 'workflow-create' ? 'creating' : busy === 'workflow-execute' ? 'executing'
            : busy === 'workflow-resume' ? 'resuming' : busy === 'workflow-read' ? 'reading'
              : busy === 'workflow-answer-read' ? 'answer-reading' : busy === 'logout' ? 'logout' : 'idle'}
          view={workflowView} error={error} onCreate={question => void createWorkflow(question)}
          onExecute={() => void executeWorkflow()} onRead={() => void readWorkflow()}
          onResume={() => void resumeWorkflow()} onReadAnswer={() => void readWorkflowAnswer()}
          onCancel={stopWaitingForWorkflow} onBack={backToDocuments}
        /> : selectedWorkspace && selectedKnowledgeBase && conversationView ? <KnowledgeConversation
          key={`${selectedWorkspace.id}/${selectedKnowledgeBase.id}`}
          workspaceName={selectedWorkspace.name} knowledgeBaseName={selectedKnowledgeBase.name}
          activity={busy === 'conversation-create' ? 'creating' : busy === 'conversation-message' ? 'sending'
            : busy === 'conversation-supplement' ? 'supplementing' : busy === 'conversation-execute' ? 'executing'
              : busy === 'conversation-read' ? 'reading' : busy === 'logout' ? 'logout' : 'idle'}
          view={conversationView} error={error} onCreate={() => void createBusinessConversation()}
          onSend={content => void sendFirstMessage(content)} onRead={() => void readCurrentConversation()}
          onSupplement={value => void supplementConversation(value)}
          onExecute={() => void executeBilling()}
          onCancel={stopWaitingForConversation} onBack={backToDocuments}
        /> : selectedWorkspace && selectedKnowledgeBase && answerOpen ? <KnowledgeAnswer
          key={`${selectedWorkspace.id}/${selectedKnowledgeBase.id}`}
          workspaceName={selectedWorkspace.name} knowledgeBaseName={selectedKnowledgeBase.name}
          activity={busy === 'answer' ? 'answering' : busy === 'logout' ? 'logout' : 'idle'}
          outcome={answerOutcome} error={error} onAnswer={question => void answer(question)}
          onCancel={stopWaitingForAnswer} onBack={backToDocuments}
        /> : selectedWorkspace && selectedKnowledgeBase && searchOpen ? <KnowledgeSearch
          key={`${selectedWorkspace.id}/${selectedKnowledgeBase.id}`}
          workspaceName={selectedWorkspace.name} knowledgeBaseName={selectedKnowledgeBase.name}
          activity={busy === 'search' ? 'searching' : busy === 'logout' ? 'logout' : 'idle'}
          outcome={searchOutcome} error={error} onSearch={query => void search(query)}
          onCancel={stopWaitingForSearch} onBack={backToDocuments}
        /> : selectedWorkspace && selectedKnowledgeBase && selectedDocument ? <DocumentProcessing
          workspaceName={selectedWorkspace.name} knowledgeBaseName={selectedKnowledgeBase.name}
          documentName={selectedDocument.originalFilename} document={documentDetail} chunks={chunks}
          error={error} outcome={chunkOutcome}
          activity={busy === 'document-detail' ? 'loading' : busy === 'chunking' ? 'chunking'
            : busy === 'embedding' ? 'embedding' : busy === 'logout' ? 'logout' : 'idle'}
          onBack={backToDocuments} onRefresh={() => void readDocument(selectedDocument.id)}
          onChunk={() => void chunkDocument()} onCancel={stopWaitingForChunking}
        >
          <DocumentEmbedding key={`${selectedWorkspace.id}/${selectedKnowledgeBase.id}/${selectedDocument.id}`}
            document={documentDetail} chunks={chunks} outcome={embeddingOutcome}
            activity={busy === 'embedding' ? 'embedding' : busy !== null || !!error ? 'blocked' : 'idle'}
            onEmbed={maxChunks => void embedDocument(maxChunks)} onCancel={stopWaitingForEmbedding} />
        </DocumentProcessing> : selectedWorkspace && selectedKnowledgeBase ? <DocumentList
          workspaceName={selectedWorkspace.name} knowledgeBaseName={selectedKnowledgeBase.name}
          documents={documents} error={error}
          activity={busy === 'documents' ? 'loading' : busy === 'logout' ? 'logout'
            : busy === 'upload' ? 'uploading' : 'idle'}
          onBack={backToKnowledgeBases} onRefresh={() => void openKnowledgeBase(selectedKnowledgeBase)}
          onSearch={openSearch}
          onAnswer={openAnswer}
          onConversation={openConversation}
          onWorkflow={openWorkflow}
          onWorkflowHistory={openWorkflowHistory}
          onOpenDocument={openDocument}
        >
          <DocumentUpload key={`${selectedWorkspace.id}/${selectedKnowledgeBase.id}`}
            workspaceName={selectedWorkspace.name} knowledgeBaseName={selectedKnowledgeBase.name}
            activity={busy === 'upload' ? 'uploading' : busy !== null || !!error ? 'blocked' : 'idle'}
            outcome={uploadOutcome} onUpload={file => void upload(file)} onCancel={stopWaitingForUpload} />
        </DocumentList> : selectedWorkspace ? <section aria-labelledby="knowledge-base-title">
          <button className="button secondary back-button" disabled={busy === 'logout'}
            onClick={backToWorkspaces}>返回工作空间</button>
          <div className="page-heading">
            <div><p className="eyebrow">工作空间：<strong>{selectedWorkspace.name}</strong></p>
              <h1 id="knowledge-base-title">知识库</h1>
              <p className="intro">这里列出该工作空间中的知识库。</p></div>
            <button className="button secondary" disabled={busy === 'knowledge-bases' || busy === 'logout'}
              onClick={() => void openWorkspace(selectedWorkspace)}>刷新列表</button>
          </div>
          {error && <div className="list-error">
            <p className="message error" role="alert">{error}</p>
            <button className="button secondary" disabled={busy === 'knowledge-bases' || busy === 'logout'}
              onClick={() => void openWorkspace(selectedWorkspace)}>重试加载</button>
          </div>}
          {busy === 'knowledge-bases' ? <p className="message" role="status">正在读取知识库…</p>
            : busy === 'logout' ? <p className="message" role="status">正在确认退出…</p>
            : error ? null : knowledgeBases.length === 0 ? <div className="empty-state">
              <h2>该工作空间暂无知识库</h2><p>这里会展示该空间中已有的知识库。</p></div>
            : <><p className="page-caption">当前页 · {knowledgeBases.length} 个知识库</p>
              <ul className="workspace-list">{knowledgeBases.map(knowledgeBase =>
                <li className="workspace-card" key={knowledgeBase.id}>
                  <span className="workspace-symbol" aria-hidden="true">K</span>
                  <div><h2>{knowledgeBase.name}</h2>
                    <button className="button secondary card-entry" aria-label={`查看${knowledgeBase.name}的文档`}
                      onClick={() => void openKnowledgeBase(knowledgeBase)}>查看文档</button></div>
                </li>,
              )}</ul>
              {knowledgeBases.length === 20 && <p className="session-note">当前页已满，可能还有更多知识库；本版仅展示第一页。</p>}
            </>}
        </section> : <section aria-labelledby="workspace-title">
          <div className="page-heading">
            <div><p className="eyebrow">工作空间</p><h1 id="workspace-title">你的工作空间</h1>
              <p className="intro">这里列出你可以访问的工作空间。</p></div>
            <button className="button secondary" disabled={busy === 'workspaces' || busy === 'logout'}
              onClick={() => void refresh()}>刷新列表</button>
          </div>
          {error && <p className="message error" role="alert">{error}</p>}
          {busy === 'workspaces' ? <p className="message" role="status">正在读取工作空间…</p>
            : error ? null : workspaces.length === 0 ? <div className="empty-state"><h2>暂无可访问的工作空间</h2>
              <p>账号尚未加入工作空间，可联系工作空间所有者添加成员。</p></div>
            : <><p className="page-caption">当前页 · {workspaces.length} 个工作空间</p>
              <ul className="workspace-list">{workspaces.map(workspace =>
                <li className="workspace-card" key={workspace.id}>
                  <span className="workspace-symbol" aria-hidden="true">W</span>
                  <div><h2>{workspace.name}</h2><p>已授权访问</p>
                    <button className="button secondary card-entry" aria-label={`进入${workspace.name}`}
                      disabled={busy === 'logout'} onClick={() => void openWorkspace(workspace)}>进入知识库</button></div>
                </li>,
              )}</ul>
              {workspaces.length === 20 && <p className="session-note">当前页已满，可能还有更多工作空间；本版仅展示第一页。</p>}
            </>}
        </section>}
      </main>
      <footer>企业知识与工作流智能体平台 · 本机演示</footer>
    </div>
  );
}
