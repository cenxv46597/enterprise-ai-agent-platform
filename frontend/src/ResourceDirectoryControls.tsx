import { useState, type FormEvent } from 'react';
import { MAX_RESOURCE_OFFSET, normalizeResourceName } from './api';

export function ResourceCreate({ kind, disabled, notice, onCreate }: {
  kind: '工作空间' | '知识库'; disabled: boolean; notice: string; onCreate: (name: string) => void;
}) {
  const [name, setName] = useState('');
  const [invalid, setInvalid] = useState('');
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) return;
    try { const normalized = normalizeResourceName(name); setInvalid(''); onCreate(normalized); }
    catch { setInvalid('请输入去掉首尾空白后 1 到 200 个字符的名称。'); }
  }
  return <form className="resource-create" noValidate onSubmit={submit}>
    <label htmlFor={`new-${kind}`}>新建{kind}名称</label>
    <input id={`new-${kind}`} value={name} disabled={disabled}
      onChange={event => { setName(event.currentTarget.value); setInvalid(''); }} />
    <button className="button primary" disabled={disabled} type="submit">创建并进入{kind}</button>
    {(invalid || notice) && <p className="message error" role="alert">{invalid || notice}</p>}
    <p className="session-note">仅创建资料容器，不调用模型。创建成功后直接进入，不受当前列表页限制。</p>
  </form>;
}

export function DirectoryPagination({ offset, count, disabled, onPage }: {
  offset: number; count: number; disabled: boolean; onPage: (offset: number) => void;
}) {
  return <nav className="directory-pagination" aria-label="资源列表翻页">
    <p className="page-caption">第 {offset / 20 + 1} 页 · 当前页 {count} 条</p>
    <button className="button secondary" disabled={disabled || offset === 0} onClick={() => onPage(0)}>首页</button>
    <button className="button secondary" disabled={disabled || offset === 0} onClick={() => onPage(Math.max(0, offset - 20))}>上一页</button>
    <button className="button secondary" disabled={disabled || count !== 20 || offset > MAX_RESOURCE_OFFSET - 20}
      onClick={() => onPage(offset + 20)}>下一页</button>
    {count === 20 && <p className="session-note">当前页已满，可能还有更多记录；点击下一页读取。</p>}
  </nav>;
}
