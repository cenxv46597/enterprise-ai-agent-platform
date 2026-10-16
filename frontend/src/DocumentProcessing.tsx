import type { DocumentChunk, DocumentStatus, KnowledgeDocument } from './api';

export type ChunkOutcome =
  | { kind: 'confirmed'; count: number }
  | { kind: 'unconfirmed' | 'rejected'; message: string };

type Props = {
  workspaceName: string;
  knowledgeBaseName: string;
  documentName: string;
  document: KnowledgeDocument | null;
  chunks: DocumentChunk[] | null;
  activity: 'idle' | 'loading' | 'chunking' | 'logout';
  error: string;
  outcome: ChunkOutcome | null;
  onBack: () => void;
  onRefresh: () => void;
  onChunk: () => void;
  onCancel: () => void;
};

const STATUS_TEXT: Record<DocumentStatus, { label: string; description: string }> = {
  uploaded: { label: '已上传', description: '原件已保存，需要先成功解析，才能切块。' },
  processing: { label: '解析中', description: '正在解析原件，需要先成功解析，才能切块。' },
  parsed: { label: '已解析', description: '文字已解析；切块不会生成向量，尚未可检索。' },
  ready: { label: '可检索', description: '完整向量已保存，可以参与知识检索。' },
  failed: { label: '解析失败', description: '原件解析失败，需要先成功解析，才能切块。' },
};

function preview(content: string): { text: string; shortened: boolean } {
  let text = '';
  let count = 0;
  for (const character of content) {
    if (count === 300) return { text, shortened: true };
    text += character;
    count += 1;
  }
  return { text, shortened: false };
}

export default function DocumentProcessing({ workspaceName, knowledgeBaseName, documentName, document,
  chunks, activity, error, outcome, onBack, onRefresh, onChunk, onCancel }: Props) {
  const busy = activity !== 'idle';
  const canChunk = !busy && !error && document !== null && chunks !== null
    && (document.status === 'parsed' || document.status === 'ready');
  const showDetails = activity !== 'logout';
  const status = document ? STATUS_TEXT[document.status] : null;

  return <section className="document-processing" aria-labelledby="document-processing-title">
    <button className="button secondary back-button" disabled={activity === 'logout'}
      onClick={onBack}>返回文档列表</button>
    <div className="page-heading">
      <div><p className="eyebrow">工作空间：<strong>{workspaceName}</strong></p>
        <h1 id="document-processing-title">文档处理</h1>
        <p className="intro">知识库：<strong>{knowledgeBaseName}</strong></p></div>
      <button className="button secondary" disabled={busy} onClick={onRefresh}>刷新文档与切块</button>
    </div>
    {error && <div className="list-error">
      <p className="message error" role="alert">{error}</p>
      <button className="button secondary" disabled={busy} onClick={onRefresh}>重试读取</button>
    </div>}
    {activity === 'loading' && <p className="message" role="status">正在读取文档与切块…</p>}
    {activity === 'logout' && <p className="message" role="status">正在确认退出…</p>}
    {showDetails && <>
      <section className="status-summary" aria-labelledby="processing-document-title">
        <h2 id="processing-document-title">{document?.originalFilename ?? documentName}</h2>
        {document && status ? <>
          <span className={`document-status status-${document.status}`}>{status.label}</span>
          <p>{status.description}</p>
        </> : <p className="message">文档最新状态尚未确认</p>}
      </section>
      <section aria-labelledby="chunk-action-title">
        <h2 id="chunk-action-title">文档切块</h2>
        <p>每块最多 500 字符，重叠 50 字符</p>
        <p className="session-note">同一配置会复用已有文本块；不同配置不会覆盖已有切块。</p>
        <div className="processing-actions">
          <button className="button primary" disabled={!canChunk} onClick={onChunk}>
            {activity === 'chunking' ? '正在切块…' : '切块'}
          </button>
          {activity === 'chunking' && <button className="button secondary"
            onClick={onCancel}>停止等待切块结果</button>}
        </div>
        {activity === 'chunking' && <>
          <p className="message" role="status">正在切块…</p>
          <p className="session-note">停止等待只影响浏览器等待，不能保证后端停止处理。请刷新核对切块记录。</p>
        </>}
      </section>
      {outcome && <section className="chunk-outcome" aria-labelledby="chunk-outcome-title">
        <h2 id="chunk-outcome-title">本次切块结果</h2>
        {outcome.kind === 'confirmed'
          ? <p role="status">切块请求成功，返回 {outcome.count} 个文本块。</p>
          : <p className="message error" role="alert">{outcome.message}</p>}
        {outcome.kind === 'unconfirmed' && <p className="session-note">
          请使用“刷新文档与切块”核对已有记录；这里不会自动重试切块请求。
        </p>}
      </section>}
      <section aria-labelledby="chunk-list-title">
        <h2 id="chunk-list-title">已有文本块</h2>
        {chunks === null ? <p className="message">切块记录尚未确认，请刷新文档与切块后再操作。</p>
          : chunks.length === 0 ? <p className="message">当前没有文本块</p>
          : <>
            <p className="page-caption">共 {chunks.length} 个文本块</p>
            <p className="session-note">仅预览前 3 个文本块，每块最多显示前 300 个字符。
              字符范围采用 [开始, 结束)，对应解析文本中的字符位置，不是文件字节位置。</p>
            <ol className="chunk-list">{chunks.slice(0, 3).map(chunk => {
              const content = preview(chunk.content);
              return <li className="chunk-card" key={chunk.id}>
                <h3>文本块 {chunk.chunkIndex}</h3>
                <p>字符范围：[{chunk.startChar}, {chunk.endChar})</p>
                <details><summary>查看文本预览</summary>
                  <pre className="chunk-preview">{content.text}</pre>
                  {content.shortened && <p className="session-note">此块正文较长，仅显示前 300 个字符。</p>}
                </details>
              </li>;
            })}</ol>
          </>}
      </section>
    </>}
  </section>;
}
