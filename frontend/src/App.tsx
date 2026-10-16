import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, listDocuments, listKnowledgeBases, listWorkspaces, login, logout, restoreSession,
  uploadDocument, UploadUnconfirmedError, UPLOAD_UNCONFIRMED_MESSAGE,
  type KnowledgeBase, type KnowledgeDocument, type Session, type Workspace } from './api';
import DocumentList from './DocumentList';
import DocumentUpload, { type UploadOutcome } from './DocumentUpload';

type Busy = 'restore' | 'login' | 'workspaces' | 'knowledge-bases' | 'documents' | 'upload' | 'logout' | null;
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
    void refresh();
  }

  async function openKnowledgeBase(knowledgeBase: KnowledgeBase) {
    if (!selectedWorkspace) return;
    const { id, controller } = beginRequest();
    setSelectedKnowledgeBase(knowledgeBase);
    setDocuments([]);
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
    if (busy === 'upload') {
      setUploadOutcome({ kind: 'unconfirmed', message: UPLOAD_UNCONFIRMED_MESSAGE });
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
        </section> : selectedWorkspace && selectedKnowledgeBase ? <DocumentList
          workspaceName={selectedWorkspace.name} knowledgeBaseName={selectedKnowledgeBase.name}
          documents={documents} error={error}
          activity={busy === 'documents' ? 'loading' : busy === 'logout' ? 'logout'
            : busy === 'upload' ? 'uploading' : 'idle'}
          onBack={backToKnowledgeBases} onRefresh={() => void openKnowledgeBase(selectedKnowledgeBase)}
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
