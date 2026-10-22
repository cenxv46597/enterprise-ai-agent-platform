import { Blob as NodeBlob } from 'node:buffer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ANSWER_EXPORT_FILENAME, buildAnswerText, downloadAnswerText } from './answer-export';
import type { AnswerTextExportInput } from './answer-export';

const DOCUMENT = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const CHUNK = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
const OTHER_DOCUMENT = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
const OTHER_CHUNK = 'ffffffff-ffff-ffff-ffff-ffffffffffff';
const ANSWER = '完整回答😀，保留必要条件与例外。\n'.repeat(130);
const QUOTE = '完整引文😀，不能截断。\n'.repeat(55);
const PRIVATE_SOURCE = 'PRIVATE_SOURCE_MUST_NOT_EXPORT';
const PRIVATE_TRACE = 'PRIVATE_TRACE_MUST_NOT_EXPORT';
const PRIVATE_ACCOUNT = 'PRIVATE_ACCOUNT_MUST_NOT_EXPORT';
const OBJECT_URL = 'blob:answer-export-fixture';
const fetchMock = vi.fn();
const createObjectURL = vi.fn<(blob: Blob) => string>();
const revokeObjectURL = vi.fn<(url: string) => void>();
const NativeURL = URL;

function input(): AnswerTextExportInput {
  return {
    workspaceName: '企业空间😀', knowledgeBaseName: '操作手册库', question: '原先提交的问题😀？\n请保留完整条件。',
    result: { answer: ANSWER, abstained: false, citations: [
      { documentId: DOCUMENT, documentName: '规程😀.md', chunkId: CHUNK, chunkIndex: 7,
        quote: QUOTE, startChar: 200, endChar: 200 + Array.from(QUOTE).length },
      { documentId: OTHER_DOCUMENT, documentName: '例外.txt', chunkId: OTHER_CHUNK, chunkIndex: 2,
        quote: '例外条款。', startChar: 10, endChar: 15 },
    ] },
  };
}

function expectCreatedUrlsRevoked() {
  const created = createObjectURL.mock.results.flatMap(result => result.type === 'return' ? [result.value] : []);
  expect(revokeObjectURL.mock.calls.map(([url]) => url)).toEqual(created);
  expect(vi.getTimerCount()).toBe(0);
}

beforeEach(() => {
  vi.useFakeTimers();
  fetchMock.mockReset(); createObjectURL.mockReset(); revokeObjectURL.mockReset();
  createObjectURL.mockReturnValue(OBJECT_URL);
  vi.stubGlobal('fetch', fetchMock);
  // Node's Blob exposes bytes in jsdom, letting the test inspect the actual UTF-8 BOM.
  vi.stubGlobal('Blob', NodeBlob);
  vi.stubGlobal('URL', Object.assign(class extends NativeURL {}, { createObjectURL, revokeObjectURL }));
});

afterEach(() => {
  vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  document.querySelectorAll('a[download]').forEach(anchor => anchor.remove());
});

describe('引用问答纯文本白名单格式', () => {
  it('保留完整Unicode答案、问题、长引文与每条来源身份和范围，build本身不带BOM', () => {
    const record = input(), text = buildAnswerText(record);
    expect(text.startsWith('\uFEFF')).toBe(false);
    for (const field of ['企业知识库问答记录', '工作空间', record.workspaceName, '知识库', record.knowledgeBaseName,
      '本次提交的问题', record.question, '回答', ANSWER, '结果', '文档', '块序号', '引用来源']) {
      expect(text).toContain(field);
    }
    expect(text).toMatch(/引用来源\s*[:：]\s*2/);
    for (const citation of record.result.citations) {
      expect(text).toContain(citation.documentName);
      expect(text).toContain(citation.documentId);
      expect(text).toContain(citation.chunkId);
      expect(text).toContain(citation.quote);
      expect(text).toMatch(new RegExp(`\\[${citation.startChar},\\s*${citation.endChar}\\)`));
      expect(text).toMatch(new RegExp(`块序号\\s*[:：]\\s*${citation.chunkIndex}`));
    }
    expect(text).toMatch(/文档\s*ID/); expect(text).toMatch(/块\s*ID/);
    expect(Array.from(ANSWER).length).toBeGreaterThan(1000);
    expect(Array.from(QUOTE).length).toBeGreaterThan(300);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it('已确认弃答明确保留弃答结果与0引用，不制造来源或正常回答结论', () => {
    const record = input();
    record.result = { answer: '目前检索到的资料不足以确认答案。', abstained: true, citations: [] };
    const text = buildAnswerText(record);
    expect(text).toContain('证据不足，本次弃答');
    expect(text).toContain(record.result.answer);
    expect(text).toMatch(/引用来源\s*[:：]\s*0/);
    expect(text).not.toContain(DOCUMENT); expect(text).not.toContain(CHUNK);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('只输出公开字段，不序列化来源预览、编号、账号及未提交输入，也不解析HTML', () => {
    const record = input();
    record.result.answer = '<script>globalThis.exportInjected = true</script>\n原样的 <b>中文😀</b> & 符号。';
    Object.assign(record, { source: { content: PRIVATE_SOURCE }, traceId: PRIVATE_TRACE,
      account: { username: PRIVATE_ACCOUNT }, currentQuestion: 'UNSUBMITTED_QUESTION' });
    Object.assign(record.result, { source: { content: PRIVATE_SOURCE }, traceId: PRIVATE_TRACE, account: PRIVATE_ACCOUNT });
    Object.assign(record.result.citations[0], { source: PRIVATE_SOURCE, traceId: PRIVATE_TRACE });
    const text = buildAnswerText(record);
    expect(text).toContain(record.result.answer);
    expect(text).toContain(record.question);
    for (const privateValue of [PRIVATE_SOURCE, PRIVATE_TRACE, PRIVATE_ACCOUNT, 'UNSUBMITTED_QUESTION']) {
      expect(text).not.toContain(privateValue);
    }
    expect(document.querySelector('script')).toBeNull();
    expect(document.querySelector('b')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled(); expect(createObjectURL).not.toHaveBeenCalled();
  });
});

describe('本地UTF-8文本下载与资源清理', () => {
  it('使用固定文件名、BOM和text/plain MIME，点击后删除anchor且延迟1000ms撤销URL', async () => {
    const record = input();
    record.workspaceName = '../../不作为文件名😀.html';
    let anchor!: HTMLAnchorElement;
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      anchor = this;
      expect(this.isConnected).toBe(true);
      expect(this.download).toBe('knowledge-answer.txt');
      expect(this.getAttribute('href')).toBe(OBJECT_URL);
      expect(revokeObjectURL).not.toHaveBeenCalled();
    });
    downloadAnswerText(record);
    expect(ANSWER_EXPORT_FILENAME).toBe('knowledge-answer.txt');
    expect(click).toHaveBeenCalledTimes(1); expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(anchor.isConnected).toBe(false);
    expect(document.querySelector('a[download]')).toBeNull();
    const blob = createObjectURL.mock.calls[0][0];
    expect(blob).toBeInstanceOf(NodeBlob);
    expect(blob.type).toBe('text/plain;charset=utf-8');
    const bytes = new Uint8Array(await blob.arrayBuffer());
    expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
    expect(new TextDecoder().decode(bytes.slice(3))).toBe(buildAnswerText(record));
    expect(revokeObjectURL).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(999);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(revokeObjectURL).toHaveBeenCalledTimes(1); expect(revokeObjectURL).toHaveBeenCalledWith(OBJECT_URL);
    expect(vi.getTimerCount()).toBe(0); expect(fetchMock).not.toHaveBeenCalled();
  });

  it('objectURL创建失败向调用方抛错，不残留anchor或安排回收', () => {
    const failure = new Error('synthetic-url-failure');
    createObjectURL.mockImplementationOnce(() => { throw failure; });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    expect(() => downloadAnswerText(input())).toThrow(failure);
    expect(click).not.toHaveBeenCalled(); expect(document.querySelector('a[download]')).toBeNull();
    expect(revokeObjectURL).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0); expect(fetchMock).not.toHaveBeenCalled();
  });

  it('createElement失败立即回收已创建URL，保持失败可观察且无延迟任务', () => {
    const failure = new Error('synthetic-element-failure');
    vi.spyOn(document, 'createElement').mockImplementationOnce(() => { throw failure; });
    expect(() => downloadAnswerText(input())).toThrow(failure);
    expectCreatedUrlsRevoked();
    expect(document.querySelector('a[download]')).toBeNull();
    expect(vi.getTimerCount()).toBe(0); expect(fetchMock).not.toHaveBeenCalled();
  });

  it('append失败即使anchor已经插入也删除它并立即回收URL', () => {
    const failure = new Error('synthetic-append-failure');
    const append = document.body.appendChild.bind(document.body);
    vi.spyOn(document.body, 'appendChild').mockImplementationOnce(node => {
      append(node);
      throw failure;
    });
    vi.spyOn(document.body, 'append').mockImplementationOnce((...nodes) => {
      for (const node of nodes) append(typeof node === 'string' ? document.createTextNode(node) : node);
      throw failure;
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    expect(() => downloadAnswerText(input())).toThrow(failure);
    expect(click).not.toHaveBeenCalled();
    expect(document.querySelector('a[download]')).toBeNull();
    expectCreatedUrlsRevoked();
    expect(vi.getTimerCount()).toBe(0); expect(fetchMock).not.toHaveBeenCalled();
  });

  it('click失败删除临时anchor并立即回收URL，不伪装成下载已触发', () => {
    const failure = new Error('synthetic-click-failure');
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => { throw failure; });
    expect(() => downloadAnswerText(input())).toThrow(failure);
    expect(document.querySelector('a[download]')).toBeNull();
    expectCreatedUrlsRevoked();
    expect(vi.getTimerCount()).toBe(0); expect(fetchMock).not.toHaveBeenCalled();
  });
});
