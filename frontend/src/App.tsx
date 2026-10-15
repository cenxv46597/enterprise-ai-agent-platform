import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, listWorkspaces, login, type Session, type Workspace } from './api';

type Busy = 'login' | 'workspaces' | null;

export default function App() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [session, setSession] = useState<Session | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);

  const reset = useCallback((message = '') => {
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    setSession(null);
    setWorkspaces([]);
    setPassword('');
    setBusy(null);
    setError(message);
  }, []);

  useEffect(() => () => {
    generation.current += 1;
    pending.current?.abort();
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

  async function refresh(current: Session) {
    const { id, controller } = beginRequest();
    setBusy('workspaces');
    setError('');
    setWorkspaces([]);
    try {
      const rows = await listWorkspaces(current.token, controller.signal);
      if (isCurrent(id, controller)) setWorkspaces(rows);
    } catch (failure) {
      if (!isCurrent(id, controller)) return;
      if (failure instanceof ApiError && failure.status === 401) {
        reset(failure.message);
      } else {
        setError(failure instanceof ApiError ? failure.message : '无法读取工作空间，请稍后重试。');
      }
    } finally {
      if (isCurrent(id, controller)) {
        setBusy(null);
        pending.current = null;
      }
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
      await refresh(authenticated);
    } catch (failure) {
      if (isCurrent(id, controller)) {
        reset(failure instanceof ApiError ? failure.message : '无法完成登录，请稍后重试。');
      }
    } finally {
      if (isCurrent(id, controller)) {
        setBusy(null);
        pending.current = null;
      }
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
          <button className="button secondary" onClick={() => reset()}>退出登录</button>
        </div>}
      </header>

      <main className={session ? 'workspace-page' : 'login-page'}>
        {!session ? <section className="login-card" aria-labelledby="login-title">
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
              onClick={() => reset('登录已取消。')}>取消登录</button>}
          </form>
          <p className="session-note">刷新页面后需要重新登录。请保留好你的账号信息。</p>
        </section> : <section aria-labelledby="workspace-title">
          <div className="page-heading">
            <div><p className="eyebrow">工作空间</p><h1 id="workspace-title">你的工作空间</h1>
              <p className="intro">这里列出你可以访问的工作空间。</p></div>
            <button className="button secondary" disabled={busy === 'workspaces'}
              onClick={() => void refresh(session)}>刷新列表</button>
          </div>
          {error && <p className="message error" role="alert">{error}</p>}
          {busy === 'workspaces' ? <p className="message" role="status">正在读取工作空间…</p>
            : error ? null : workspaces.length === 0 ? <div className="empty-state"><h2>暂无可访问的工作空间</h2>
              <p>账号尚未加入工作空间，可联系工作空间所有者添加成员。</p></div>
            : <><p className="page-caption">当前页 · {workspaces.length} 个工作空间</p>
              <ul className="workspace-list">{workspaces.map(workspace =>
                <li className="workspace-card" key={workspace.id}>
                  <span className="workspace-symbol" aria-hidden="true">W</span>
                  <div><h2>{workspace.name}</h2><p>已授权访问</p></div>
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
