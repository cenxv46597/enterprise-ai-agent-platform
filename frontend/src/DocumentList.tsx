import type { ReactNode } from 'react';
import { DOCUMENT_FILENAME_QUERY_ERROR, DOCUMENT_PAGE_SIZE, isDocumentOffset, isDocumentStatusFilter,
  normalizeDocumentFilenameQuery, type DocumentStatus, type DocumentStatusFilter, type KnowledgeDocument } from './api';

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
  activity: 'idle' | 'loading' | 'uploading' | 'logout';
  error: string;
  onBack: () => void;
  onRefresh: () => void;
  onSearch?: () => void;
  onAnswer?: () => void;
  onConversation?: () => void;
  onWorkflow?: () => void;
  onWorkflowHistory?: () => void;
  onOpenDocument: (document: KnowledgeDocument) => void;
  filenameDraft?: string;
  filenameQuery?: string;
  statusDraft?: DocumentStatusFilter;
  statusFilter?: DocumentStatusFilter;
  offset?: number;
  onFilenameDraftChange?: (value: string) => void;
  onStatusDraftChange?: (value: DocumentStatusFilter) => void;
  onFind?: () => void;
  onClearFilename?: () => void;
  onPage?: (offset: number) => void;
  children?: ReactNode;
};

export default function DocumentList({ workspaceName, knowledgeBaseName, documents,
  activity, error, onBack, onRefresh, onSearch, onAnswer, onConversation, onWorkflow, onWorkflowHistory, onOpenDocument,
  filenameDraft = '', filenameQuery = '', statusDraft = 'all', statusFilter = 'all', offset = 0,
  onFilenameDraftChange, onStatusDraftChange, onFind, onClearFilename,
  onPage, children }: Props) {
  const busy = activity !== 'idle';
  const readyCount = documents.filter(document => document.status === 'ready').length;
  const validOffset = isDocumentOffset(offset);
  const canGoBack = validOffset && offset >= DOCUMENT_PAGE_SIZE;
  const canGoNext = validOffset && !error && documents.length === DOCUMENT_PAGE_SIZE
    && isDocumentOffset(offset + DOCUMENT_PAGE_SIZE);
  let filenameError = '';
  try { normalizeDocumentFilenameQuery(filenameDraft); }
  catch { filenameError = DOCUMENT_FILENAME_QUERY_ERROR; }
  return <section aria-labelledby="document-title">
    <button className="button secondary back-button" disabled={activity === 'logout'}
      onClick={onBack}>返回知识库</button>
    <div className="page-heading">
      <div><p className="eyebrow">工作空间：<strong>{workspaceName}</strong></p>
        <h1 id="document-title">文档</h1>
        <p className="intro">知识库：<strong>{knowledgeBaseName}</strong></p></div>
      <div className="page-actions">
        {onWorkflowHistory && <button className="button secondary" disabled={busy || !!error}
          onClick={onWorkflowHistory}>工作流记录</button>}
        {onWorkflow && <button className="button secondary" disabled={busy || !!error}
          onClick={onWorkflow}>问答工作流</button>}
        {onConversation && <button className="button secondary" disabled={busy || !!error}
          onClick={onConversation}>会话问答</button>}
        {onAnswer && <button className="button primary" disabled={busy || !!error}
          onClick={onAnswer}>引用问答</button>}
        {onSearch && <button className="button secondary" disabled={busy || !!error}
          onClick={onSearch}>检索知识库</button>}
        <button className="button secondary" disabled={busy} onClick={onRefresh}>刷新列表</button>
      </div>
    </div>
    {activity !== 'logout' && children}
    {activity !== 'logout' && onFind && <section className="search-form-panel document-find-panel"
      aria-labelledby="document-find-title">
      <h2 id="document-find-title">查找知识库文档</h2>
      <p id="document-find-note">按文件名包含的文字和文档状态查找当前知识库中的文档，两项条件同时生效。
        输入或选择后点击查找；这里查找文件名，不检索正文，也不调用模型。
        查找条件只影响文档列表，引用问答仍面向当前整个知识库。</p>
      <form onSubmit={event => {
        event.preventDefault();
        if (!busy && !filenameError) onFind();
      }}>
        <label htmlFor="document-filename-query">按文件名查找</label>
        <input id="document-filename-query" value={filenameDraft} disabled={busy}
          aria-invalid={!!filenameError}
          aria-describedby={`document-find-note${filenameError ? ' document-filename-error' : ''}`}
          placeholder="例如：报销"
          onChange={event => { if (!busy) onFilenameDraftChange?.(event.target.value); }} />
        {filenameError && <p id="document-filename-error" className="message error" role="alert">{filenameError}</p>}
        <label htmlFor="document-status-filter">文档状态</label>
        <select id="document-status-filter" value={statusDraft} disabled={busy}
          aria-describedby="document-find-note" onChange={event => {
            if (!busy && isDocumentStatusFilter(event.target.value)) onStatusDraftChange?.(event.target.value);
          }}>
          <option value="all">全部状态</option>
          {(Object.keys(STATUS_TEXT) as DocumentStatus[]).map(status =>
            <option key={status} value={status}>{STATUS_TEXT[status].label}</option>)}
        </select>
        <div className="processing-actions">
          <button className="button primary" type="submit" disabled={busy || !!filenameError}>查找</button>
          {onClearFilename && <button className="button secondary" type="button"
            disabled={busy || (!filenameDraft && !filenameQuery && statusDraft === 'all' && statusFilter === 'all')}
            onClick={() => { if (!busy) onClearFilename(); }}>清除查找</button>}
        </div>
      </form>
      <p className="page-caption submitted-query">{filenameQuery
        ? <>已应用的文件名条件：<strong>{filenameQuery}</strong></> : '已应用的文件名条件：不限文件名'}；
        已应用的文档状态：<strong>{statusFilter === 'all' ? '全部状态' : STATUS_TEXT[statusFilter].label}</strong>。
        刷新列表保留已应用的条件和当前页；查找或清除后从首页读取。</p>
    </section>}
    {activity !== 'logout' && onPage && <>
      <nav className="processing-actions" aria-label="文档列表翻页">
        <button className="button secondary" disabled={busy || !canGoBack}
          onClick={() => { if (!busy && canGoBack) onPage(offset - DOCUMENT_PAGE_SIZE); }}>上一页</button>
        <button className="button secondary" disabled={busy || !canGoNext}
          onClick={() => { if (!busy && canGoNext) onPage(offset + DOCUMENT_PAGE_SIZE); }}>下一页</button>
        <button className="button secondary" disabled={busy || !canGoBack}
          onClick={() => { if (!busy && canGoBack) onPage(0); }}>回到首页</button>
      </nav>
      <p className="page-caption">当前页：第 {(validOffset ? offset : 0) / DOCUMENT_PAGE_SIZE + 1} 页，每页最多 {DOCUMENT_PAGE_SIZE} 份。</p>
    </>}
    {error && <div className="list-error">
      <p className="message error" role="alert">{error}</p>
      <button className="button secondary" disabled={busy} onClick={onRefresh}>重试加载</button>
    </div>}
    {activity === 'loading' ? <p className="message" role="status">正在读取文档…</p>
      : activity === 'logout' ? <p className="message" role="status">正在确认退出…</p>
      : error ? null : documents.length === 0 ? <div className="empty-state">
        <h2>{offset > 0 ? '当前页没有文档' : statusFilter !== 'all' ? '未找到符合查找条件的文档'
          : filenameQuery ? '未找到文件名匹配的文档' : '该知识库暂无文档'}</h2>
        <p>{offset > 0 ? '本次读取的这一页没有文档，可返回上一页或回到首页。'
          : statusFilter !== 'all' ? '请调整文件名或文档状态，或清除查找后查看该知识库的文档。'
            : filenameQuery ? '请调整文件名线索，或清除查找后查看该知识库的文档。'
            : '这里会展示该知识库中已有的文档。'}</p></div>
      : <><p className="page-caption">当前页 · {documents.length} 份文档 · {readyCount} 份可检索 · {statusFilter === 'all'
        ? '全部处理状态' : STATUS_TEXT[statusFilter].label}</p>
        <ul className="workspace-list">{documents.map(document => {
          const status = STATUS_TEXT[document.status];
          return <li className="workspace-card" key={document.id}>
            <span className="workspace-symbol" aria-hidden="true">D</span>
            <div><h2>{document.originalFilename}</h2>
              <span className={`document-status status-${document.status}`}>{status.label}</span>
              <p className="document-description">{status.description}</p>
              <button className="button secondary card-entry" disabled={busy}
                aria-label={`查看${document.originalFilename}的处理详情`}
                onClick={() => onOpenDocument(document)}>查看与切块</button></div>
          </li>;
        })}</ul>
        {documents.length === DOCUMENT_PAGE_SIZE && <p className="session-note">{onPage
          ? '当前页已满，可能还有更多文档；下一页也可能为空。'
          : '当前页已满，可能还有更多文档；本版仅展示第一页。'}</p>}
      </>}
    {activity !== 'logout' && onPage && <p className="session-note">文档按创建时间从旧到新排列；浏览期间若有文档新增或删除，
      跨页可能出现重复或遗漏。可回到首页重新浏览。</p>}
  </section>;
}
