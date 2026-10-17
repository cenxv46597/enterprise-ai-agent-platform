import { useState, type FormEvent } from 'react';
import type { KnowledgeAnswerResult } from './api';
import { canResumeWorkflow, type WorkflowCreation, type WorkflowMeta, type WorkflowReceipt, type WorkflowResume } from './workflow-api';

export type WorkflowView = {
  creation: WorkflowCreation | null;
  created: WorkflowMeta | null;
  createNotice: string;
  execution: { requestId: string; expectedVersion: 0 } | null;
  receipt: WorkflowReceipt | null;
  executeNotice: string;
  snapshot: WorkflowMeta | null;
  readNotice: string;
  blocked: boolean;
  resumeRequest: WorkflowResume | null;
  resumeReceipt: WorkflowReceipt | null;
  resumeNotice: string;
};

type Props = {
  workspaceName: string;
  knowledgeBaseName: string;
  activity: 'idle' | 'creating' | 'executing' | 'resuming' | 'reading' | 'logout';
  view: WorkflowView;
  error: string;
  onCreate: (question: string) => void;
  onExecute: () => void;
  onRead: () => void;
  onResume: () => void;
  onCancel: () => void;
  onBack: () => void;
};

const STATUS_TEXT: Record<WorkflowMeta['status'], string> = {
  pending: '已创建，尚未执行',
  running: '执行中',
  failed: '执行失败',
  completed: '已完成',
};

const STEP_TEXT: Record<WorkflowMeta['nextStep'], string> = {
  retrieve: '检索资料', generate: '生成回答', done: '处理完成',
};

const ERROR_TEXT: Record<string, string> = {
  embedding_timeout: '问题向量服务等待超时',
  embedding_error: '问题向量服务失败',
  retrieval_conflict: '资料检索发生冲突',
  retrieval_invalid: '检索结果未通过校验',
  generation_configuration: '回答服务配置不可用',
  generation_timeout: '回答服务等待超时',
  generation_error: '回答服务失败',
  evidence_changed: '引用资料已变化',
  checkpoint_invalid: '保存的检查点未通过校验',
  context_limit: '正文超过当前任务预算',
  service_error: '服务处理失败',
  execution_interrupted: '执行中断',
};

function Metadata({ workflow }: { workflow: WorkflowMeta }) {
  return <>
    <p>记录状态：<strong>{STATUS_TEXT[workflow.status]}</strong>；
      当前步骤：{STEP_TEXT[workflow.nextStep]}。</p>
    <p>记录版本：{workflow.version}；已记录尝试次数：{workflow.attemptCount}。</p>
    <p>创建时冻结的正文字符预算：{workflow.maxContextCharacters} 个字符。</p>
    <p>{workflow.checkpointCount === null ? '尚无已保存的检索检查点。'
      : `检索检查点已保存 ${workflow.checkpointCount} 个文本块。`}</p>
    {workflow.errorCode !== null && <p className="message error">
      失败原因：{Object.hasOwn(ERROR_TEXT, workflow.errorCode)
        ? ERROR_TEXT[workflow.errorCode] : '原因未识别，未展示原始内容'}。</p>}
    {workflow.status === 'failed' && <p className="session-note">{workflow.retryable
      ? '后端允许后续显式恢复；是否开放操作仍需核对当前状态及页面范围。'
      : '当前记录不允许恢复；本页不会再次执行。'}</p>}
    <p className="session-note">固定步骤为“检索资料 → 保存检查点 → 生成回答 → 完成”。
      以上是已收到的记录，不是实时进度；记录版本也不等于执行次数。</p>
  </>;
}

function Answer({ answer }: { answer: KnowledgeAnswerResult }) {
  if (answer.abstained) return <div className="empty-state">
    <h3>证据不足，本次弃答</h3>
    <p className="answer-text">{answer.answer}</p>
    <p className="session-note">工作流已完成处理，但没有足够证据生成受支持的回答。</p>
  </div>;
  return <>
    <div className="answer-content"><h3>工作流回答</h3>
      <p className="answer-text">{answer.answer}</p></div>
    <h3>引用来源 · {answer.citations.length} 条</h3>
    <p className="session-note">以下为完整引文；字符范围对应解析文本中的 [开始, 结束)，
      不是文件字节位置。引用存在仍需结合原文判断回答是否受到支持。</p>
    <ol className="answer-citation-list">{answer.citations.map((citation, index) =>
      <li className="answer-citation" key={`${citation.chunkId}/${index}`}>
        <h4>引用 {index + 1} · {citation.documentName}</h4>
        <p className="page-caption">文本块 {citation.chunkIndex} · 引文字符范围：
          [{citation.startChar}, {citation.endChar})</p>
        <blockquote className="answer-quote">{citation.quote}</blockquote>
      </li>,
    )}</ol>
  </>;
}

export default function KnowledgeWorkflow({ workspaceName, knowledgeBaseName, activity,
  view, error, onCreate, onExecute, onRead, onResume, onCancel, onBack }: Props) {
  const [question, setQuestion] = useState('');
  const [validationError, setValidationError] = useState('');
  const busy = activity !== 'idle';
  const canCreate = !busy && !view.creation && !view.created && !view.blocked;
  const canExecute = !busy && !view.blocked && !!view.created && !view.execution
    && !view.receipt && view.created.status === 'pending' && view.created.version === 0;
  const canResume = !busy && !view.resumeRequest && !view.readNotice && canResumeWorkflow(view.receipt, view.snapshot);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canCreate) return;
    const normalized = question.trim();
    let length = 0;
    for (const _character of normalized) {
      length += 1;
      if (length > 2000) break;
    }
    if (length < 1 || length > 2000) {
      setValidationError('请输入去掉首尾空白后 1 到 2000 个字符的工作流问题。');
      return;
    }
    setValidationError('');
    onCreate(normalized);
  }

  return <section className="knowledge-workflow" aria-labelledby="workflow-title">
    <button className="button secondary back-button" disabled={activity === 'logout'}
      onClick={onBack}>返回文档列表</button>
    <div className="page-heading"><div>
      <p className="eyebrow">工作空间：<strong>{workspaceName}</strong></p>
      <h1 id="workflow-title">问答工作流</h1>
      <p className="intro">知识库：<strong>{knowledgeBaseName}</strong></p>
    </div></div>
    {error && <p className="message error" role="alert">{error}</p>}
    {activity === 'logout' ? <p className="message" role="status">正在确认退出…</p> : <>
      <div className="search-form-panel">
        <h2>创建固定问答工作流</h2>
        <p id="workflow-scope">资料范围为当前知识库，不限于文档列表第一页。
          创建成功后仍需手动执行，创建本身不会自动生成回答。</p>
        <form noValidate onSubmit={submit} aria-busy={activity === 'creating'}>
          <label htmlFor="workflow-question">工作流问题</label>
          <textarea id="workflow-question" rows={3} value={view.creation?.question ?? question}
            disabled={!canCreate} aria-describedby="workflow-scope workflow-create-help"
            aria-invalid={validationError ? true : undefined}
            onChange={event => { setQuestion(event.currentTarget.value); setValidationError(''); }} />
          <p className="session-note" id="workflow-create-help">问题去掉首尾空白后应为 1 到 2000 个字符。
            提交后保留这次问题，本页不重复创建。</p>
          {validationError && <p className="message error" role="alert">{validationError}</p>}
          <div className="processing-actions">
            <button className="button primary" type="submit" disabled={!canCreate}>
              {activity === 'creating' ? '正在创建工作流…' : '创建工作流'}</button>
            {activity === 'creating' && <button className="button secondary" type="button" onClick={onCancel}>
              停止等待创建结果</button>}
          </div>
        </form>
        {view.creation && <p className="submitted-query">本次提交的问题：<strong>{view.creation.question}</strong></p>}
        {activity === 'creating' && <p className="session-note">
          停止等待仅取消浏览器等待，不能保证后端没有创建工作流。</p>}
        {view.createNotice && <p className="message error" role="alert">{view.createNotice}</p>}
      </div>
      {view.created && <section className="search-form-panel" aria-labelledby="workflow-execute-title">
        <h2 id="workflow-execute-title">手动执行一次</h2>
        <p className="message">工作流已确认创建。创建操作不会自动执行。</p>
        <p>创建时冻结的正文字符预算：{view.created.maxContextCharacters} 个字符。</p>
        <p className="session-note">点击执行后依次检索资料、保存检查点、生成回答。
          执行可能调用向量服务和回答模型；本页不自动重试。
          已确认的生成失败可按下方条件明确恢复一次。</p>
        <div className="processing-actions">
          <button className="button primary" disabled={!canExecute} onClick={() => { if (canExecute) onExecute(); }}>
            {activity === 'executing' ? '正在执行工作流…' : '执行工作流一次'}</button>
          {activity === 'executing' && <button className="button secondary" onClick={onCancel}>停止等待执行结果</button>}
        </div>
        {activity === 'executing' && <p className="message" role="status">正在执行并核对保存结果与引用来源…</p>}
        {activity === 'executing' && <p className="session-note">
          停止等待不能保证后端停止或没有产生费用。</p>}
        {view.execution && <p className="session-note">首次执行提交已保留，不会重发。
          读取状态不会自动重发或解锁首次执行；恢复须另行明确确认。</p>}
        {view.executeNotice && <p className="message error" role="alert">{view.executeNotice}</p>}
      </section>}
      {view.receipt?.status === 'failed' && !view.resumeRequest && <section className="search-form-panel" aria-label="生成失败恢复">
        <h2>从检查点恢复生成</h2>
        <p>本步仅处理本页已确认的生成失败，且需保留检索检查点。
          请先手动读取当前状态，核对版本、尝试次数、失败阶段和原检查点一致。</p>
        {canResume ? <RecoveryConfirmation key={view.snapshot!.version} onResume={onResume} /> :
          <p className="session-note">当前尚未满足恢复条件。读取状态不会自动执行；
            检索失败、执行中、结果未确认或资料变化的任务不能在这里恢复。</p>}
      </section>}
      {view.resumeRequest && <section className="search-form-panel" aria-label="恢复提交记录">
        <h2>本次恢复提交</h2>
        <p>按已核对的版本 {view.resumeRequest.expectedVersion} 明确提交，复用检索检查点继续生成。</p>
        {activity === 'resuming' && <>
          <p role="status">正在恢复生成并核对保存结果与全部来源…</p>
          <button className="button secondary" onClick={onCancel}>停止等待恢复结果</button>
          <p className="session-note">停止等待不能保证后端停止或没有产生新的调用费用。</p>
        </>}
        <p className="session-note">本页仅恢复一次，不重发该请求；读取状态不会开放再次恢复。</p>
        {view.resumeNotice && <p className="message error" role="alert">{view.resumeNotice}</p>}
      </section>}
      {view.resumeReceipt && <section className="answer-results" aria-label="恢复执行回执">
        <h2>恢复执行回执</h2>
        <p>恢复回执：{view.resumeReceipt.status === 'completed' ? '处理完成' : '执行失败'}；
          已记录尝试次数：{view.resumeReceipt.attemptNumber}。</p>
        <Metadata workflow={view.resumeReceipt.workflow} />
        {view.resumeReceipt.status === 'completed' && view.resumeReceipt.answer
          ? <Answer answer={view.resumeReceipt.answer} /> : <p className="message error">本次恢复没有完整回答，本页不继续恢复。</p>}
      </section>}
      {view.receipt && <section className="answer-results" aria-labelledby="workflow-receipt-title">
        <h2 id="workflow-receipt-title">本次执行回执</h2>
        <p className="session-note">这是本次执行已确认的历史回执；下方“当前工作流状态”单独展示手动读取的记录。</p>
        <p>回执结果：{view.receipt.status === 'completed' ? '处理完成' : '执行失败'}；本次为第 {view.receipt.attemptNumber} 次尝试。</p>
        <Metadata workflow={view.receipt.workflow} />
        {view.receipt.status === 'completed' && view.receipt.answer
          ? <Answer answer={view.receipt.answer} />
          : <p className="message error" role="alert">本次没有可展示的完整回答。</p>}
      </section>}
      {view.created && <section className="conversation-snapshot" aria-labelledby="workflow-state-title">
        <h2 id="workflow-state-title">当前工作流状态</h2>
        <button className="button secondary" disabled={busy} onClick={onRead}>读取当前工作流状态</button>
        {activity === 'reading' && <button className="button secondary conversation-cancel" onClick={onCancel}>停止读取状态</button>}
        {activity === 'reading' && <p role="status">正在读取当前工作流状态…</p>}
        {view.readNotice && <p className="message error" role="alert">{view.readNotice}</p>}
        {view.snapshot ? <Metadata workflow={view.snapshot} /> : <p>尚无本次有效读取记录。</p>}
        <p className="session-note">这里只读取状态元数据，不恢复答案、引文或本次执行回执。
          即使记录仍未变化，原请求也可能在处理；不能据此直接重发。</p>
      </section>}
    </>}
  </section>;
}

function RecoveryConfirmation({ onResume }: { onResume: () => void }) {
  const [confirmed, setConfirmed] = useState(false);
  return <div className="conversation-billing-confirmation">
    <p>将复用已经保存的检索结果，后端仍会重新核对原文来源；不会自动重新检索。
      继续生成可能产生新的调用费用，先前超时的调用也不能据此判断未计费。</p>
    <label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.currentTarget.checked)} />
      我已核对当前失败状态，允许本次恢复可能产生新的生成调用</label>
    <button className="button primary" disabled={!confirmed} onClick={() => { if (confirmed) onResume(); }}>
      确认并恢复生成一次</button>
    <p className="session-note">勾选不会提交，点击按钮才发起恢复；不接管仍在执行或结果未知的请求。</p>
  </div>;
}
