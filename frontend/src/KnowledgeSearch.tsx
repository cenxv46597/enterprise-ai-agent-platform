import { useState, type FormEvent } from 'react';
import { isSearchStrategy, type KnowledgeSearchResult, type SearchStrategy } from './api';

export type SearchOutcome =
  | { kind: 'confirmed'; query: string; result: KnowledgeSearchResult }
  | { kind: 'unconfirmed' | 'rejected'; message: string };

type Props = {
  workspaceName: string;
  knowledgeBaseName: string;
  activity: 'idle' | 'searching' | 'logout';
  outcome: SearchOutcome | null;
  error: string;
  onSearch: (query: string, strategy: SearchStrategy) => void;
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
  const [strategy, setStrategy] = useState<SearchStrategy>('vector');
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
    onSearch(normalized, strategy);
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
        <p id="search-scope">检索范围为当前知识库中的全部可检索文档，不限于文档列表页或文件名、状态筛选结果。</p>
        <form noValidate onSubmit={submit} aria-busy={activity === 'searching'}>
          <label htmlFor="search-query">检索问题</label>
          <textarea id="search-query" rows={3} value={query} disabled={busy}
            aria-describedby="search-scope search-help" aria-invalid={validationError ? true : undefined}
            onChange={event => { setQuery(event.currentTarget.value); setValidationError(''); }} />
          <label htmlFor="search-strategy">检索方式</label>
          <select id="search-strategy" value={strategy} disabled={busy} aria-describedby="search-strategy-help"
            onChange={event => {
              if (!busy && isSearchStrategy(event.currentTarget.value)) setStrategy(event.currentTarget.value);
            }}>
            <option value="vector">向量检索</option>
            <option value="hybrid">混合检索（向量＋词项）</option>
          </select>
          <p className="session-note" id="search-strategy-help">向量检索侧重语义接近；混合检索结合向量与词项匹配的名次，
            可用于错误码、版本号等文字线索。选择方式不会发送请求，点击“检索”后才生效。
            该选择只影响本页，引用问答仍使用向量检索。</p>
          {strategy === 'hybrid' && <p className="session-note">当前混合检索支持最多 2000 个可检索文本块、100 万正文字符。
            超限时拒绝，不截取部分知识库，也不自动换方式重试。</p>}
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
          <p className="submitted-query">本次提交的检索方式：<strong>{outcome.result.strategy === 'hybrid'
            ? '混合检索（向量＋词项）' : '向量检索'}</strong> · 未启用重排。</p>
          <p className="page-caption">返回 {outcome.result.hits.length} 个候选段落，最多 3 个。
            这里只展示检索候选，尚未生成答案。</p>
          {outcome.result.hits.length === 0 ? <div className="empty-state">
            <h3>本次未返回候选段落</h3>
            <p>这不代表知识库没有文档，也不能据此确认本次没有产生费用。</p>
          </div> : <>
            <p className="session-note">{outcome.result.strategy === 'hybrid'
              ? '候选按融合得分从高到低排列；融合得分综合两路名次，不是答案正确率。余弦距离仍表示向量接近程度，越小越接近，不决定本次排列。“未入前20”只说明该通道未选中此候选，不代表完全不相关。'
              : '余弦距离越小表示向量越接近，不是答案正确率。'}
              每段仅预览前 300 个字符；字符范围为解析文本中的 [开始, 结束)，不是文件字节位置。</p>
            <ol className="search-hit-list">{outcome.result.hits.map((hit, index) =>
              <li className="search-hit" key={hit.chunkId}>
                <h3>候选 {index + 1} · {hit.documentName}</h3>
                {outcome.result.strategy === 'hybrid' && <p className="page-caption">
                  融合得分：{hit.fusionScore?.toFixed(6)} · 向量通道名次：{hit.vectorRank ?? '未入前20'}
                  {' · '}词项通道名次：{hit.lexicalRank ?? '未入前20'}</p>}
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
