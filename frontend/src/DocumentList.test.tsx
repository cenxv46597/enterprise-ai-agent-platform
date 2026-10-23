import type { ComponentProps } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DOCUMENT_FILENAME_QUERY_ERROR, MAX_DOCUMENT_OFFSET,
  type DocumentStatus, type DocumentStatusFilter, type KnowledgeDocument } from './api';
import DocumentList from './DocumentList';

const DOCUMENT: KnowledgeDocument = {
  id: '00000000-0000-0000-0000-000000000301',
  knowledgeBaseId: '00000000-0000-0000-0000-000000000101',
  originalFilename: '报销制度.md', status: 'ready',
};
const FULL_PAGE = Array.from({ length: 20 }, (_, index) => ({
  ...DOCUMENT, id: `00000000-0000-0000-0000-${String(index).padStart(12, '0')}`,
  originalFilename: `报销制度${index}.md`,
}));

function props(overrides: Partial<ComponentProps<typeof DocumentList>> = {}): ComponentProps<typeof DocumentList> {
  return {
    workspaceName: '甲空间', knowledgeBaseName: '制度库', documents: [DOCUMENT], activity: 'idle', error: '',
    onBack: vi.fn(), onRefresh: vi.fn(), onOpenDocument: vi.fn(),
    filenameDraft: '', filenameQuery: '', statusDraft: 'all', statusFilter: 'all', offset: 0,
    onFilenameDraftChange: vi.fn(), onStatusDraftChange: vi.fn(), onFind: vi.fn(), onClearFilename: vi.fn(), onPage: vi.fn(),
    ...overrides,
  };
}

function button(name: string): HTMLButtonElement {
  return screen.getByRole<HTMLButtonElement>('button', { name });
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('document filename and pagination controls', () => {
  it('edits a controlled draft without requesting and only submits through explicit actions', () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal('fetch', fetchMock);
    const current = props({ filenameDraft: '报销', filenameQuery: '研发', offset: 20 });
    render(<DocumentList {...current} />);
    fireEvent.change(screen.getByLabelText('按文件名查找'), { target: { value: '新资料' } });
    expect(current.onFilenameDraftChange).toHaveBeenCalledWith('新资料');
    expect(current.onFind).not.toHaveBeenCalled();
    expect(current.onPage).not.toHaveBeenCalled();
    expect(screen.getByText(/已应用的文件名条件/).textContent).toContain('研发');
    expect(screen.getByText(/当前页：第 2 页/)).toBeTruthy();
    fireEvent.click(button('查找'));
    expect(current.onFind).toHaveBeenCalledTimes(1);
    fireEvent.click(button('清除查找'));
    expect(current.onClearFilename).toHaveBeenCalledTimes(1);
    fireEvent.click(button('刷新列表'));
    expect(current.onRefresh).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('edits only the controlled state draft and leaves the applied condition unchanged until explicit find', () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal('fetch', fetchMock);
    const current = props({ filenameDraft: '尚未提交', filenameQuery: '报销', statusDraft: 'parsed',
      statusFilter: 'failed', documents: [{ ...DOCUMENT, status: 'failed' }] });
    render(<DocumentList {...current} />);
    expect(screen.getAllByRole('option').map(option => option.textContent)).toEqual([
      '全部状态', '已上传', '解析中', '已解析', '可检索', '解析失败',
    ]);
    const status = screen.getByLabelText<HTMLSelectElement>('文档状态');
    expect(status.value).toBe('parsed');
    fireEvent.change(status, { target: { value: 'ready' } });
    expect(current.onStatusDraftChange).toHaveBeenCalledOnce();
    expect(current.onStatusDraftChange).toHaveBeenCalledWith('ready');
    expect(current.onFilenameDraftChange).not.toHaveBeenCalled();
    expect(current.onFind).not.toHaveBeenCalled();
    expect(current.onPage).not.toHaveBeenCalled();
    expect(status.value).toBe('parsed');
    expect(screen.getByText(/已应用的文档状态/).textContent).toContain('已应用的文档状态：解析失败');
    expect(screen.getByText(/查找条件只影响文档列表/).textContent).toContain('引用问答仍面向当前整个知识库');
    fireEvent.click(button('查找'));
    expect(current.onFind).toHaveBeenCalledOnce();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each<[DocumentStatusFilter, DocumentStatusFilter, boolean]>([
    ['all', 'all', true], ['failed', 'all', false], ['all', 'parsed', false], ['ready', 'failed', false],
  ])('clears state-only conditions with draft %s and applied %s using the compatible clear action',
    (statusDraft, statusFilter, disabled) => {
      const current = props({ statusDraft, statusFilter });
      render(<DocumentList {...current} />);
      expect(button('清除查找').disabled).toBe(disabled);
      fireEvent.click(button('清除查找'));
      expect(current.onClearFilename).toHaveBeenCalledTimes(disabled ? 0 : 1);
      expect(current.onStatusDraftChange).not.toHaveBeenCalled();
      expect(current.onFind).not.toHaveBeenCalled();
    });

  it.each<[DocumentStatus, string]>([
    ['uploaded', '已上传'], ['processing', '解析中'], ['parsed', '已解析'], ['ready', '可检索'], ['failed', '解析失败'],
  ])('describes only this page and the applied %s state, regardless of the unsubmitted state draft',
    (statusFilter, label) => {
      render(<DocumentList {...props({ statusFilter, statusDraft: 'all', documents: [{ ...DOCUMENT, status: statusFilter }] })} />);
      expect(screen.getByText(`当前页 · 1 份文档 · ${statusFilter === 'ready' ? 1 : 0} 份可检索 · ${label}`)).toBeTruthy();
      expect(screen.getByText(/已应用的文档状态/).textContent).toContain(`已应用的文档状态：${label}`);
      expect(screen.queryByText(/全部处理状态/)).toBeNull();
    });

  it.each(['😀'.repeat(256), '报\u0000销'])('blocks an invalid draft while allowing it to be cleared', draft => {
    const current = props({ filenameDraft: draft });
    render(<DocumentList {...current} />);
    expect(screen.getByRole('alert').textContent).toBe(DOCUMENT_FILENAME_QUERY_ERROR);
    expect(screen.getByLabelText('按文件名查找').getAttribute('aria-invalid')).toBe('true');
    expect(button('查找').disabled).toBe(true);
    fireEvent.submit(screen.getByLabelText('按文件名查找').closest('form')!);
    expect(current.onFind).not.toHaveBeenCalled();
    expect(button('清除查找').disabled).toBe(false);
    fireEvent.click(button('清除查找'));
    expect(current.onClearFilename).toHaveBeenCalledTimes(1);
  });

  it.each(['😀'.repeat(255), ' \t '])('accepts Unicode code points and blank drafts', draft => {
    const current = props({ filenameDraft: draft });
    render(<DocumentList {...current} />);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(button('查找').disabled).toBe(false);
    fireEvent.submit(screen.getByLabelText('按文件名查找').closest('form')!);
    expect(current.onFind).toHaveBeenCalledTimes(1);
  });

  it('shows current-page counts and makes bounded twenty-item page requests', () => {
    const current = props({ documents: FULL_PAGE, offset: 20, filenameQuery: '报销' });
    render(<DocumentList {...current} />);
    expect(screen.getByText('当前页 · 20 份文档 · 20 份可检索 · 全部处理状态')).toBeTruthy();
    expect(screen.getByText(/下一页也可能为空/)).toBeTruthy();
    fireEvent.click(button('上一页'));
    fireEvent.click(button('下一页'));
    fireEvent.click(button('回到首页'));
    expect(current.onPage).toHaveBeenNthCalledWith(1, 0);
    expect(current.onPage).toHaveBeenNthCalledWith(2, 40);
    expect(current.onPage).toHaveBeenNthCalledWith(3, 0);
  });

  it('distinguishes an empty library, no filename match and an empty later page', () => {
    const current = props({ documents: [] });
    const { rerender } = render(<DocumentList {...current} />);
    expect(screen.getByRole('heading', { name: '该知识库暂无文档' })).toBeTruthy();
    expect(button('上一页').disabled).toBe(true);
    expect(button('下一页').disabled).toBe(true);
    expect(button('回到首页').disabled).toBe(true);
    rerender(<DocumentList {...current} filenameQuery="报销" />);
    expect(screen.getByRole('heading', { name: '未找到文件名匹配的文档' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '该知识库暂无文档' })).toBeNull();
    rerender(<DocumentList {...current} statusFilter="failed" />);
    expect(screen.getByRole('heading', { name: '未找到符合查找条件的文档' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '该知识库暂无文档' })).toBeNull();
    rerender(<DocumentList {...current} filenameQuery="报销" statusFilter="failed" />);
    expect(screen.getByRole('heading', { name: '未找到符合查找条件的文档' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '未找到文件名匹配的文档' })).toBeNull();
    rerender(<DocumentList {...current} filenameQuery="报销" statusFilter="failed" offset={40} />);
    expect(screen.getByRole('heading', { name: '当前页没有文档' })).toBeTruthy();
    expect(button('上一页').disabled).toBe(false);
    expect(button('回到首页').disabled).toBe(false);
    expect(button('下一页').disabled).toBe(true);
    fireEvent.click(button('上一页'));
    expect(current.onPage).toHaveBeenCalledWith(20);
  });

  it('keeps a failed target page and query, hides old rows and permits previous or home navigation', () => {
    const current = props({ documents: FULL_PAGE, filenameDraft: '还没提交', filenameQuery: '报销',
      statusDraft: 'parsed', statusFilter: 'failed', offset: 20, error: '请求未完成，请稍后重试。' });
    render(<DocumentList {...current} />);
    expect(screen.getByRole('alert').textContent).toBe(current.error);
    expect(screen.getByText(/当前页：第 2 页/)).toBeTruthy();
    expect(screen.getByText(/已应用的文件名条件/).textContent).toContain('报销');
    expect(screen.getByText(/已应用的文档状态/).textContent).toContain('已应用的文档状态：解析失败');
    expect(screen.getByLabelText<HTMLSelectElement>('文档状态').value).toBe('parsed');
    expect(screen.queryByRole('heading', { name: '报销制度0.md' })).toBeNull();
    expect(button('下一页').disabled).toBe(true);
    expect(button('上一页').disabled).toBe(false);
    expect(button('回到首页').disabled).toBe(false);
    fireEvent.click(button('重试加载'));
    expect(current.onRefresh).toHaveBeenCalledTimes(1);
  });

  it.each(['loading', 'uploading'] as const)('disables all search and page mutations during %s', activity => {
    const current = props({ activity, documents: FULL_PAGE, filenameDraft: '报销', offset: 20 });
    render(<DocumentList {...current} />);
    expect(screen.getByLabelText<HTMLInputElement>('按文件名查找').disabled).toBe(true);
    expect(screen.getByLabelText<HTMLSelectElement>('文档状态').disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('文档状态'), { target: { value: 'failed' } });
    for (const name of ['查找', '清除查找', '上一页', '下一页', '回到首页', '刷新列表']) {
      expect(button(name).disabled).toBe(true);
      fireEvent.click(button(name));
    }
    expect(current.onFind).not.toHaveBeenCalled();
    expect(current.onStatusDraftChange).not.toHaveBeenCalled();
    expect(current.onClearFilename).not.toHaveBeenCalled();
    expect(current.onPage).not.toHaveBeenCalled();
    expect(current.onRefresh).not.toHaveBeenCalled();
    expect(button('返回知识库').disabled).toBe(false);
  });

  it('hides the search, pagination and upload children during logout', () => {
    render(<DocumentList {...props({ activity: 'logout' })}><p>上传区域</p></DocumentList>);
    expect(screen.queryByLabelText('按文件名查找')).toBeNull();
    expect(screen.queryByLabelText('文档状态')).toBeNull();
    expect(screen.queryByRole('navigation', { name: '文档列表翻页' })).toBeNull();
    expect(screen.queryByText('上传区域')).toBeNull();
    expect(button('返回知识库').disabled).toBe(true);
    expect(screen.getByRole('status').textContent).toBe('正在确认退出…');
  });

  it('does not step beyond a safe integer page or enable next on a partial page', () => {
    const current = props({ documents: FULL_PAGE, offset: MAX_DOCUMENT_OFFSET });
    const { rerender } = render(<DocumentList {...current} />);
    expect(button('下一页').disabled).toBe(true);
    expect(button('上一页').disabled).toBe(false);
    fireEvent.click(button('下一页'));
    expect(current.onPage).not.toHaveBeenCalled();
    rerender(<DocumentList {...current} documents={[DOCUMENT]} offset={0} />);
    expect(button('下一页').disabled).toBe(true);
  });

  it('preserves the legacy rendering when the new optional controls are absent', () => {
    render(<DocumentList workspaceName="甲空间" knowledgeBaseName="制度库" documents={FULL_PAGE}
      activity="idle" error="" onBack={vi.fn()} onRefresh={vi.fn()} onOpenDocument={vi.fn()} />);
    expect(screen.queryByLabelText('按文件名查找')).toBeNull();
    expect(screen.queryByLabelText('文档状态')).toBeNull();
    expect(screen.queryByRole('navigation', { name: '文档列表翻页' })).toBeNull();
    expect(screen.getByText(/本版仅展示第一页/)).toBeTruthy();
  });
});
