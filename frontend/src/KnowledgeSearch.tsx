import { useState, type FormEvent } from 'react';
import type { KnowledgeSearchResult } from './api';

export type SearchOutcome =
  | { kind: 'confirmed'; query: string; result: KnowledgeSearchResult }
  | { kind: 'unconfirmed' | 'rejected'; message: string };

type Props = {
  workspaceName: string;
  knowledgeBaseName: string;
  activity: 'idle' | 'searching' | 'logout';
  outcome: SearchOutcome | null;
  error: string;
  onSearch: (query: string) => void;
  onCancel: () => void;
  onBack: () => void;
};

function preview(content: string) {
  let text = '';
  let count = 0;
  for (const character of content) {
    if (count === 300) return `${text}…`;
    text += character;
    count += 1;
  }
  return text;
}

export default function KnowledgeSearch({ workspaceName, knowledgeBaseName, activity,
  outcome, error, onSearch, onCancel, onBack }: Props) {
  const [query, setQuery] = useState('');
  const [validationError, setValidationError] = useState('');
  const busy = activity !== 'idle';

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const normalized = query.trim();
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
    onSearch(normalized);
  }

  return <section className="knowledge-search" aria-labelledby="search-title">
    <button className="button secondary back-button" disabled={activity === 'logout'}
      onClick={onBack}>返回文档列表</button>
    <div className="page-heading">
      <div><p className="eyebrow">工作空间：<strong>{workspaceName}</strong></p>
        <h1 id="search-title">检索知识库</h1>
        <p className="intro">知识库：<strong>{knowledgeBaseName}</strong></p></div>
    </div>
    {error && <p className="message error" role="alert">{error}</p>}
    {activity === 'logout' ? <p className="message" role="status">正在确认退出…</p> : <>
      <div className="search-form-panel">
        <p id="search-scope">检索范围为当前知识库中的全部可检索文档，不限于文档列表第一页。</p>
        <form noValidate onSubmit={submit} aria-busy={activity === 'searching'}>
          <label htmlFor="search-query">检索问题</label>
          <textarea id="search-query" rows={3} value={query} disabled={busy}
            aria-describedby="search-scope search-help" aria-invalid={validationError ? true : undefined}
            onChange={event => { setQuery(event.currentTarget.value); setValidationError(''); }} />
          <p className="session-note" id="search-help">填写问题后，点击“检索”才会提交。
            本次操作可能调用问题向量服务；不自动重试。</p>
          {validationError && <p className="message error" role="alert">{validationError}</p>}
          <div className="processing-actions">
            <button className="button primary" type="submit" disabled={busy}>
              {activity === 'searching' ? '正在检索…' : '检索'}</button>
            {activity === 'searching' && <button className="button secondary" type="button"
              onClick={onCancel}>停止等待检索结果</button>}
          </div>
        </form>
      </div>
      {activity === 'searching' && <div>
        <p className="message" role="status">正在检索并核对来源文档…</p>
        <p className="session-note">停止等待仅取消浏览器等待，不能保证后端停止或没有产生费用。</p>
      </div>}
      {outcome && <section className="search-results" aria-labelledby="search-result-title">
        <h2 id="search-result-title">本次检索结果</h2>
        {outcome.kind === 'confirmed' ? <>
          <p className="submitted-query">本次提交的问题：<strong>{outcome.query}</strong></p>
          <p className="page-caption">返回 {outcome.result.hits.length} 个候选段落，最多 3 个。
            这里只展示检索候选，尚未生成答案。</p>
          {outcome.result.hits.length === 0 ? <div className="empty-state">
            <h3>本次未返回候选段落</h3>
            <p>这不代表知识库没有文档，也不能据此确认本次没有产生费用。</p>
          </div> : <>
            <p className="session-note">余弦距离越小表示向量越接近，不是答案正确率。
              每段仅预览前 300 个字符；字符范围为解析文本中的 [开始, 结束)，不是文件字节位置。</p>
            <ol className="search-hit-list">{outcome.result.hits.map((hit, index) =>
              <li className="search-hit" key={hit.chunkId}>
                <h3>候选 {index + 1} · {hit.documentName}</h3>
                <p className="page-caption">文本块 {hit.chunkIndex} · 字符范围：[{hit.startChar}, {hit.endChar})
                  {' · '}余弦距离：{hit.distance.toFixed(4)}</p>
                <p className="search-preview">{preview(hit.content)}</p>
              </li>,
            )}</ol>
          </>}
        </> : <p className="message error" role="alert">{outcome.message}</p>}
      </section>}
    </>}
  </section>;
}
