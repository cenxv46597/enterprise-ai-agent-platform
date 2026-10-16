import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import DocumentUpload from './DocumentUpload';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('取消选择清空文件框，保留已有上传结果，并允许重新选择同一文件而不上传', async () => {
  const user = userEvent.setup();
  const onUpload = vi.fn();
  const onCancel = vi.fn();
  const fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal('fetch', fetchMock);

  render(<DocumentUpload workspaceName="甲空间" knowledgeBaseName="产品知识库"
    activity="idle" outcome={{ kind: 'confirmed', document: {
      id: '00000000-0000-0000-0000-000000000301',
      knowledgeBaseId: '00000000-0000-0000-0000-000000000101',
      originalFilename: '已有上传结果.md',
      status: 'parsed',
      parseError: null,
    } }} onUpload={onUpload} onCancel={onCancel} />);

  const input = screen.getByLabelText<HTMLInputElement>('选择文档');
  const upload = screen.getByRole<HTMLButtonElement>('button', { name: /^上传$/ });
  const result = screen.getByRole('region', { name: '本次上传结果' });
  const file = new File(['待上传的说明内容'], '待上传说明.md', { type: 'text/markdown' });

  await user.upload(input, file);
  expect(screen.getByText(/^已选择：/)).toBeTruthy();
  expect(input.files?.item(0)).toBe(file);
  expect(input.value).not.toBe('');
  expect(upload.disabled).toBe(false);

  await user.click(screen.getByRole('button', { name: '取消选择' }));

  expect(screen.queryByText(/^已选择：/)).toBeNull();
  expect(input.value).toBe('');
  expect(input.files?.length).toBe(0);
  expect(upload.disabled).toBe(true);
  expect(screen.queryByRole('button', { name: '取消选择' })).toBeNull();
  expect(within(result).getByText('已有上传结果.md')).toBeTruthy();
  expect(within(result).getByText('已解析')).toBeTruthy();
  expect(onUpload).not.toHaveBeenCalled();
  expect(onCancel).not.toHaveBeenCalled();
  expect(fetchMock).not.toHaveBeenCalled();

  await user.upload(input, file);

  expect(screen.getByText(/^已选择：/)).toBeTruthy();
  expect(input.files?.item(0)).toBe(file);
  expect(upload.disabled).toBe(false);
  expect(within(result).getByText('已有上传结果.md')).toBeTruthy();
  expect(onUpload).not.toHaveBeenCalled();
  expect(onCancel).not.toHaveBeenCalled();
  expect(fetchMock).not.toHaveBeenCalled();
});
