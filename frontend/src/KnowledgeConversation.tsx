import { useState, type FormEvent } from 'react';
import type {
  CreatedConversation, ConversationSnapshot, FirstMessageRequest, FirstConversationReply,
} from './conversation-api';

type Notice = { kind: 'unconfirmed' | 'rejected'; message: string };
export type ConversationView = {
  createAttempted: boolean;
  created: CreatedConversation | null;
  createOutcome: Notice | null;
  request: FirstMessageRequest | null;
  messageOutcome: { kind: 'confirmed'; result: FirstConversationReply } | Notice | null;
  snapshot: ConversationSnapshot | null;
  readError: string;
};

type Props = {
  workspaceName: string;
  knowledgeBaseName: string;
  activity: 'idle' | 'creating' | 'sending' | 'reading' | 'logout';
  view: ConversationView;
  error: string;
  onCreate: () => void;
  onSend: (content: string) => void;
  onRead: () => void;
  onCancel: () => void;
  onBack: () => void;
};

const STATE_TEXT: Record<ConversationSnapshot['status'], string> = {
  idle: '没有待补任务', waiting_for_user: '等待补充条件', ready: '输入已齐备', completed: '标准规则估算已完成',
};

function FirstReply({ result }: { result: FirstConversationReply }) {
  const answer = result.answer;
  const pending = result.pending;
  const ruleSourceIds = new Set(pending?.requirements.flatMap(item => item.evidenceIds));
  return <>
    {result.calculationCount > 0 && <p className="session-note">
      本轮包含 {result.calculationCount} 次已执行算术操作；此处展示本轮回复，未核对算术明细。</p>}
    {result.kind === 'answered' && answer ? answer.abstained ? <div className="empty-state">
      <h3>证据不足，本轮弃答</h3><p className="answer-text">{answer.answer}</p>
    </div> : <>
      <div className="answer-content"><h3>本轮回答</h3><p className="answer-text">{answer.answer}</p></div>
      <h3>引用来源 · {answer.citations.length} 条</h3>
      <p className="session-note">以下为完整引文，字符范围对应引文在解析文本中的位置。
        引用存在仍需结合原文判断回答是否受到支持。</p>
      <ol className="answer-citation-list">{answer.citations.map((citation, index) =>
        <li className="answer-citation" key={`${citation.chunkId}/${index}`}>
          <h4>引用 {index + 1} · {citation.documentName}</h4>
          <p className="page-caption">文本块 {citation.chunkIndex} · 引文字符范围：
            [{citation.startChar}, {citation.endChar})</p>
          <blockquote className="answer-quote">{citation.quote}</blockquote>
        </li>,
      )}</ol>
    </> : result.kind === 'waiting' && pending ? <div className="conversation-pending">
      <h3>等待补充条件</h3>
      <p>当前需要补充：<strong>{pending.current.label}</strong>，
        范围 {pending.current.minValue} 到 {pending.current.maxValue}，单位：{pending.current.unit}。</p>
      <p>规则原文：</p>
      {Array.from(new Set(pending.requirements.map(item => item.ruleQuote))).map(quote =>
        <blockquote className="answer-quote" key={quote}>{quote}</blockquote>,
      )}
      <ul>{pending.requirements.map(item => <li key={item.fieldName}>
        {item.label}{item.fieldName === pending.current.fieldName ? '（当前待补）' : ''}：
        {item.minValue} 到 {item.maxValue}，单位：{item.unit}
      </li>)}</ul>
      {pending.kind === 'billing' && <p>上门次数与计费月份需要分别确认，不会根据“全年”自动填写12个月。</p>}
      <p className="session-note">规则来源：{pending.sources.filter(source => ruleSourceIds.has(source.evidenceId)).map(source =>
        `${source.documentName} · 文本块 ${source.chunkIndex}`).join('；')}。
        本页尚不提交补充条件，业务任务尚未完成。</p>
    </div> : result.kind === 'failed' ? <div className="message error" role="alert">
      <h3>本轮运行失败</h3><p>首条消息已保存，但本轮运行失败，没有可展示的完整答案。</p>
    </div> : result.kind === 'stopped' ? <div className="message" role="status">
      <h3>本轮运行已停止</h3><p>首条消息已保存，但本轮运行已停止，业务任务尚未完成。</p>
    </div> : null}
  </>;
}

export default function KnowledgeConversation({ workspaceName, knowledgeBaseName, activity,
  view, error, onCreate, onSend, onRead, onCancel, onBack }: Props) {
  const [content, setContent] = useState('');
  const [validationError, setValidationError] = useState('');
  const busy = activity !== 'idle';

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !view.created || view.request) return;
    const normalized = content.trim();
    let length = 0;
    for (const _character of normalized) {
      length += 1;
      if (length > 2000) break;
    }
    if (length < 1 || length > 2000) {
      setValidationError('请输入去掉首尾空白后 1 到 2000 个字符的首条消息。');
      return;
    }
    setValidationError('');
    onSend(normalized);
  }

  return <section className="knowledge-conversation" aria-labelledby="conversation-title">
    <button className="button secondary back-button" disabled={activity === 'logout'}
      onClick={onBack}>返回文档列表</button>
    <div className="page-heading"><div>
      <p className="eyebrow">工作空间：<strong>{workspaceName}</strong></p>
      <h1 id="conversation-title">会话问答</h1>
      <p className="intro">知识库：<strong>{knowledgeBaseName}</strong></p>
    </div></div>
    {error && <p className="message error" role="alert">{error}</p>}
    {activity === 'logout' ? <p className="message" role="status">正在确认退出…</p> : <>
      <div className="search-form-panel">
        <h2>创建业务会话</h2>
        <p>先创建当前知识库内的会话，再发送首条消息。本页仅处理首条消息；业务会话与网页登录期限分别管理。</p>
        <button className="button secondary" disabled={busy || view.createAttempted} onClick={onCreate}>
          {activity === 'creating' ? '正在创建会话…' : '创建会话'}</button>
        {activity === 'creating' && <button className="button secondary conversation-cancel" onClick={onCancel}>
          停止等待创建结果</button>}
        {activity === 'creating' && <p className="session-note">停止等待不能保证后端没有创建会话。</p>}
        {view.createOutcome && <p className="message error" role="alert">{view.createOutcome.message}</p>}
        {view.created && <p className="message" role="status">会话已创建。{view.request ? '已发起首条消息。' : '尚未发送消息。'}</p>}
        {view.createOutcome?.kind === 'unconfirmed' && <p className="session-note">
          当前没有可确认的会话记录，不能继续发送消息。重新创建可能留下另一份空会话。</p>}
      </div>
      {view.created && <div className="search-form-panel conversation-message-panel">
        <p id="conversation-scope">范围为当前知识库中的全部可检索资料，不限于文档列表第一页。</p>
        <form noValidate onSubmit={submit} aria-busy={activity === 'sending'}>
          <label htmlFor="conversation-content">首条消息</label>
          <textarea id="conversation-content" rows={3} value={content} disabled={busy || !!view.request}
            aria-describedby="conversation-scope conversation-help" aria-invalid={validationError ? true : undefined}
            onChange={event => { setContent(event.currentTarget.value); setValidationError(''); }} />
          <p className="session-note" id="conversation-help">点击“发送首条消息”才会提交。
            本轮可能调用多次模型或检索服务，不自动重试，也不自动执行补充后的任务。</p>
          {validationError && <p className="message error" role="alert">{validationError}</p>}
          <div className="processing-actions">
            <button className="button primary" type="submit" disabled={busy || !!view.request}>
              {activity === 'sending' ? '正在处理首条消息…' : '发送首条消息'}</button>
            {activity === 'sending' && <button className="button secondary" type="button" onClick={onCancel}>
              停止等待消息结果</button>}
          </div>
        </form>
        {activity === 'sending' && <p className="message" role="status">正在处理消息并核对来源…</p>}
        {activity === 'sending' && <p className="session-note">
          停止等待仅取消浏览器等待，不能保证后端停止或没有产生费用。</p>}
        {view.request && <p className="session-note">本页不会再次发送这条消息；读取状态不会自动重发。</p>}
      </div>}
      {view.messageOutcome && <section className="answer-results" aria-labelledby="conversation-result-title">
        <h2 id="conversation-result-title">本轮会话结果</h2>
        {view.messageOutcome.kind === 'confirmed' ? <>
          <p className="submitted-query">本次提交的消息：<strong>{view.request?.content}</strong></p>
          <FirstReply result={view.messageOutcome.result} />
        </> : <p className="message error" role="alert">{view.messageOutcome.message}</p>}
      </section>}
      {view.created && view.request && <section className="conversation-snapshot" aria-labelledby="conversation-state-title">
        <h2 id="conversation-state-title">当前会话状态</h2>
        <button className="button secondary" disabled={busy} onClick={onRead}>读取当前会话状态</button>
        {activity === 'reading' && <button className="button secondary conversation-cancel" onClick={onCancel}>停止读取状态</button>}
        {activity === 'reading' && <p role="status">正在读取当前会话状态…</p>}
        {view.readError && <p className="message error" role="alert">{view.readError}</p>}
        {view.snapshot && <p className="message">当前记录状态：{STATE_TEXT[view.snapshot.status]}；已保存轮次：{view.snapshot.version}。</p>}
        <p className="session-note">读取只能核对当前状态，不能确认这条消息是否已提交，也不会恢复本次完整回复和引用。
          原请求仍可能在执行；即使已保存轮次未变化，也不能直接重发。</p>
      </section>}
    </>}
  </section>;
}
