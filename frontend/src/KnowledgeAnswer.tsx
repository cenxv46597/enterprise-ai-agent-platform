import { useState, type FormEvent } from 'react';
import type { KnowledgeAnswerResult } from './api';

export type AnswerOutcome =
  | { kind: 'confirmed'; question: string; result: KnowledgeAnswerResult }
  | { kind: 'unconfirmed' | 'rejected'; message: string; traceId?: string | null };

type Props = {
  workspaceName: string;
  knowledgeBaseName: string;
  activity: 'idle' | 'answering' | 'logout';
  outcome: AnswerOutcome | null;
  error: string;
  onAnswer: (question: string) => void;
  onCancel: () => void;
  onBack: () => void;
};

export default function KnowledgeAnswer({ workspaceName, knowledgeBaseName, activity,
  outcome, error, onAnswer, onCancel, onBack }: Props) {
  const [question, setQuestion] = useState('');
  const [validationError, setValidationError] = useState('');
  const busy = activity !== 'idle';

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const normalized = question.trim();
    let length = 0;
    for (const _character of normalized) {
      length += 1;
      if (length > 2000) break;
    }
    if (length === 0 || length > 2000) {
      setValidationError('请输入去掉首尾空白后 1 到 2000 个字符的问题。');
      return;
    }
    setValidationError('');
    onAnswer(normalized);
  }

  return <section className="knowledge-answer" aria-labelledby="answer-title">
    <button className="button secondary back-button" disabled={activity === 'logout'}
      onClick={onBack}>返回文档列表</button>
    <div className="page-heading">
      <div><p className="eyebrow">工作空间：<strong>{workspaceName}</strong></p>
        <h1 id="answer-title">引用问答</h1>
        <p className="intro">知识库：<strong>{knowledgeBaseName}</strong></p></div>
    </div>
    {error && <p className="message error" role="alert">{error}</p>}
    {activity === 'logout' ? <p className="message" role="status">正在确认退出…</p> : <>
      <div className="search-form-panel">
        <p id="answer-scope">问答范围为当前知识库中的全部可检索文档，不限于文档列表第一页。</p>
        <form noValidate onSubmit={submit} aria-busy={activity === 'answering'}>
          <label htmlFor="answer-question">问答问题</label>
          <textarea id="answer-question" rows={3} value={question} disabled={busy}
            aria-describedby="answer-scope answer-help" aria-invalid={validationError ? true : undefined}
            onChange={event => { setQuestion(event.currentTarget.value); setValidationError(''); }} />
          <p className="session-note" id="answer-help">填写问题后，点击“生成回答”才会提交。
            本次操作可能调用问题向量服务和回答模型；不自动重试。</p>
          {validationError && <p className="message error" role="alert">{validationError}</p>}
          <div className="processing-actions">
            <button className="button primary" type="submit" disabled={busy}>
              {activity === 'answering' ? '正在回答…' : '生成回答'}</button>
            {activity === 'answering' && <button className="button secondary" type="button"
              onClick={onCancel}>停止等待回答结果</button>}
          </div>
        </form>
      </div>
      {activity === 'answering' && <div>
        <p className="message" role="status">正在生成回答并核对引用来源…</p>
        <p className="session-note">停止等待仅取消浏览器等待，不能保证后端停止或没有产生费用。</p>
      </div>}
      {outcome && <section className="answer-results" aria-labelledby="answer-result-title">
        <h2 id="answer-result-title">本次问答结果</h2>
        {outcome.kind === 'confirmed' ? <>
          <p className="submitted-query">本次提交的问题：<strong>{outcome.question}</strong></p>
          {outcome.result.abstained ? <div className="empty-state">
            <h3>证据不足，本次弃答</h3>
            <p className="answer-text">{outcome.result.answer}</p>
          </div> : <>
            <div className="answer-content"><h3>回答</h3>
              <p className="answer-text">{outcome.result.answer}</p></div>
            <h3>引用来源 · {outcome.result.citations.length} 条</h3>
            <p className="session-note">以下展示完整引文；字符范围对应引文在解析文本中的 [开始, 结束)，
              不是文件字节位置。引用存在仍需结合原文判断回答是否受到支持。</p>
            <ol className="answer-citation-list">{outcome.result.citations.map((citation, index) =>
              <li className="answer-citation" key={`${citation.chunkId}/${index}`}>
                <h4>引用 {index + 1} · {citation.documentName}</h4>
                <p className="page-caption">文本块 {citation.chunkIndex} · 引文字符范围：
                  [{citation.startChar}, {citation.endChar})</p>
                <blockquote className="answer-quote">{citation.quote}</blockquote>
              </li>,
            )}</ol>
          </>}
        </> : <p className="message error" role="alert">{outcome.message}
          {outcome.traceId && <><br /><span>排错编号：<code>{outcome.traceId}</code></span></>}
        </p>}
      </section>}
    </>}
  </section>;
}
