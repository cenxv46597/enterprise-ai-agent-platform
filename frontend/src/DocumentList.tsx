import type { DocumentStatus, KnowledgeDocument } from './api';

const STATUS_TEXT: Record<DocumentStatus, { label: string; description: string }> = {
  uploaded: { label: '已上传', description: '原件已保存，尚未开始解析。' },
  processing: { label: '解析中', description: '正在解析原件，尚不能保证已有可用正文。' },
  parsed: { label: '已解析', description: '文字解析成功；是否已切块需另查，完整向量尚未保存。' },
  ready: { label: '可检索', description: '完整向量已保存，可以参与知识检索。' },
  failed: { label: '解析失败', description: '原件解析失败。' },
};

type Props = {
  workspaceName: string;
  knowledgeBaseName: string;
  documents: KnowledgeDocument[];
  activity: 'idle' | 'loading' | 'logout';
  error: string;
  onBack: () => void;
  onRefresh: () => void;
};

export default function DocumentList({ workspaceName, knowledgeBaseName, documents,
  activity, error, onBack, onRefresh }: Props) {
  const busy = activity !== 'idle';
  const readyCount = documents.filter(document => document.status === 'ready').length;
  return <section aria-labelledby="document-title">
    <button className="button secondary back-button" disabled={activity === 'logout'}
      onClick={onBack}>返回知识库</button>
    <div className="page-heading">
      <div><p className="eyebrow">工作空间：<strong>{workspaceName}</strong></p>
        <h1 id="document-title">文档</h1>
        <p className="intro">知识库：<strong>{knowledgeBaseName}</strong></p></div>
      <button className="button secondary" disabled={busy} onClick={onRefresh}>刷新列表</button>
    </div>
    {error && <div className="list-error">
      <p className="message error" role="alert">{error}</p>
      <button className="button secondary" disabled={busy} onClick={onRefresh}>重试加载</button>
    </div>}
    {activity === 'loading' ? <p className="message" role="status">正在读取文档…</p>
      : activity === 'logout' ? <p className="message" role="status">正在确认退出…</p>
      : error ? null : documents.length === 0 ? <div className="empty-state">
        <h2>该知识库暂无文档</h2><p>这里会展示该知识库中已有的文档。</p></div>
      : <><p className="page-caption">当前页 · {documents.length} 份文档 · {readyCount} 份可检索 · 全部处理状态</p>
        <ul className="workspace-list">{documents.map(document => {
          const status = STATUS_TEXT[document.status];
          return <li className="workspace-card" key={document.id}>
            <span className="workspace-symbol" aria-hidden="true">D</span>
            <div><h2>{document.originalFilename}</h2>
              <span className={`document-status status-${document.status}`}>{status.label}</span>
              <p className="document-description">{status.description}</p></div>
          </li>;
        })}</ul>
        {documents.length === 20 && <p className="session-note">当前页已满，可能还有更多文档；本版仅展示第一页。</p>}
      </>}
  </section>;
}
