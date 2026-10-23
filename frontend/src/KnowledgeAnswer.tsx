import { useState, type FormEvent } from 'react';
import { isAnswerTopK, isSearchStrategy, type AnswerTopK, type DocumentChunk, type KnowledgeAnswerResult, type SearchStrategy } from './api';

export type AnswerSourceView = {
  index: number;
  chunk: DocumentChunk | null;
  notice: { message: string; traceId: string | null } | null;
};

export type AnswerOutcome =
  | { kind: 'confirmed'; question: string; submittedStrategy: SearchStrategy; submittedTopK: AnswerTopK;
      result: KnowledgeAnswerResult; source?: AnswerSourceView;
      exportStatus?: 'requested' | 'failed' }
  | { kind: 'unconfirmed' | 'rejected'; message: string; traceId?: string | null };

type Props = {
  workspaceName: string;
  knowledgeBaseName: string;
  activity: 'idle' | 'answering' | 'reading-source' | 'logout';
  outcome: AnswerOutcome | null;
  error: string;
  onAnswer: (question: string, strategy: SearchStrategy, topK: AnswerTopK) => void;
  onCancel: () => void;
  onBack: () => void;
  onReadSource?: (index: number) => void;
  onCancelSource?: () => void;
  onCloseSource?: () => void;
  onExport?: () => void;
};

export default function KnowledgeAnswer({ workspaceName, knowledgeBaseName, activity,
  outcome, error, onAnswer, onCancel, onBack, onReadSource, onCancelSource, onCloseSource, onExport }: Props) {
  const [question, setQuestion] = useState('');
  const [strategy, setStrategy] = useState<SearchStrategy>('vector');
  const [topK, setTopK] = useState<AnswerTopK>(3);
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
    onAnswer(normalized, strategy, topK);
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
        <p id="answer-scope">问答范围为当前知识库中的全部可检索文档，不限于文档列表页或文件名、状态筛选结果。</p>
        <form noValidate onSubmit={submit} aria-busy={busy}>
          <label htmlFor="answer-question">问答问题</label>
          <textarea id="answer-question" rows={3} value={question} disabled={busy}
            aria-describedby="answer-scope answer-help" aria-invalid={validationError ? true : undefined}
            onChange={event => { setQuestion(event.currentTarget.value); setValidationError(''); }} />
          <label htmlFor="answer-strategy">问答检索方式</label>
          <select id="answer-strategy" value={strategy} disabled={busy} aria-describedby="answer-strategy-help"
            onChange={event => {
              if (!busy && isSearchStrategy(event.currentTarget.value)) setStrategy(event.currentTarget.value);
            }}>
            <option value="vector">向量检索</option>
            <option value="hybrid">混合检索（向量＋词项）</option>
          </select>
          <p className="session-note" id="answer-strategy-help">向量检索侧重语义接近；混合检索同时参考词项匹配的名次。
            问答页与检索页分别选择方式；切换方式不发送请求，点击“生成回答”才按所选方式检索并生成。
            混合方式不保证回答更准确。</p>
          <label htmlFor="answer-top-k">候选段落上限</label>
          <select id="answer-top-k" value={topK} disabled={busy} aria-describedby="answer-top-k-help"
            onChange={event => {
              const selected = Number(event.currentTarget.value);
              if (!busy && isAnswerTopK(selected)) setTopK(selected);
            }}>
            <option value={3}>最多 3 段（默认）</option>
            <option value={5}>最多 5 段</option>
          </select>
          <p className="session-note" id="answer-top-k-help">这是本次检索的候选上限；上下文预算可能跳过候选，
            回答也不一定引用每段。选 5 可能增加处理时间和费用，不保证证据充分；修改选择本身不发送请求。</p>
          {strategy === 'hybrid' && <p className="session-note">当前混合检索支持最多 2000 个可检索文本块、100 万正文字符。
            超限时拒绝，不截取部分知识库，也不自动换方式重试。</p>}
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
          <p className="submitted-query">本次提交的检索方式：<strong>{outcome.submittedStrategy === 'hybrid'
            ? '混合检索（向量＋词项）' : '向量检索'}</strong> · 未请求重排。</p>
          <p className="submitted-query">本次提交的候选上限：最多 {outcome.submittedTopK} 段。</p>
          <div className="answer-export">
            <button className="button secondary" type="button" disabled={busy || !!error || !onExport}
              onClick={onExport}>导出回答与引文</button>
            <p className="session-note">下载文本文件，包含本次提交的问题、检索方式、回答和完整引文；不会重新生成回答。</p>
            {outcome.exportStatus === 'requested' && <p className="message" role="status">
              已请求浏览器下载，请在下载列表中确认文件。</p>}
            {outcome.exportStatus === 'failed' && <p className="message error" role="alert">
              未能开始下载，请重试导出。原回答仍保留。</p>}
          </div>
          {outcome.result.abstained ? <div className="empty-state">
            <h3>证据不足，本次弃答</h3>
            <p className="answer-text">{outcome.result.answer}</p>
            <p className="session-note">这只表示本次检索到的资料不足以确认答案；不代表知识库没有相关资料。</p>
          </div> : <>
            <div className="answer-content"><h3>回答</h3>
              <p className="answer-text">{outcome.result.answer}</p></div>
            <h3>引用来源 · {outcome.result.citations.length} 条</h3>
            <p className="session-note">以下展示完整引文；字符范围对应引文在解析文本中的 [开始, 结束)，
              不是文件字节位置。引用存在仍需结合原文判断回答是否受到支持。</p>
            <ol className="answer-citation-list">{outcome.result.citations.map((citation, index) => {
              const source = outcome.source?.index === index ? outcome.source : null;
              const characters = source?.chunk ? Array.from(source.chunk.content) : null;
              const quoteStart = source?.chunk ? citation.startChar - source.chunk.startChar : 0;
              const quoteEnd = source?.chunk ? citation.endChar - source.chunk.startChar : 0;
              return <li className="answer-citation" key={`${citation.chunkId}/${index}`}>
                <h4>引用 {index + 1} · {citation.documentName}</h4>
                <p className="page-caption">文本块 {citation.chunkIndex} · 引文字符范围：
                  [{citation.startChar}, {citation.endChar})</p>
                <blockquote className="answer-quote">{citation.quote}</blockquote>
                <button className="button secondary" type="button" disabled={busy || !onReadSource}
                  aria-label={`查看引用 ${index + 1} 的完整来源段落`} aria-expanded={source !== null}
                  onClick={() => onReadSource?.(index)}>
                  {source?.chunk ? '重新读取完整来源段落' : '查看完整来源段落'}</button>
                {source && <section className="answer-source" aria-label={`引用 ${index + 1} 的完整来源段落`}>
                  {activity === 'reading-source' && <>
                    <p className="message" role="status">正在读取来源段落…</p>
                    <button className="button secondary" type="button" onClick={onCancelSource}>停止读取来源</button>
                  </>}
                  {source.notice && <p className="message error" role="alert">{source.notice.message}
                    {source.notice.traceId && <><br /><span>排错编号：<code>{source.notice.traceId}</code></span></>}
                  </p>}
                  {source.chunk && characters && <>
                    <p className="page-caption">完整文本块 {source.chunk.chunkIndex} · 字符范围：
                      [{source.chunk.startChar}, {source.chunk.endChar})</p>
                    <p className="session-note">本条引文与本次读取的来源段落一致，标亮部分是引文。
                      这里展示一个完整文本块，请结合上下文判断回答是否准确。</p>
                    <pre className="chunk-preview" tabIndex={0} aria-label="完整来源正文">{characters.slice(0, quoteStart).join('')}
                      <mark>{characters.slice(quoteStart, quoteEnd).join('')}</mark>
                      {characters.slice(quoteEnd).join('')}</pre>
                  </>}
                  <button className="button secondary" type="button" onClick={onCloseSource}>关闭来源段落</button>
                </section>}
              </li>;
            })}</ol>
          </>}
        </> : <p className="message error" role="alert">{outcome.message}
          {outcome.traceId && <><br /><span>排错编号：<code>{outcome.traceId}</code></span></>}
        </p>}
      </section>}
    </>}
  </section>;
}
