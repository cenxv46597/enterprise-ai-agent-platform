import type { KnowledgeAnswerResult } from './api';

export type AnswerTextExportInput = {
  workspaceName: string;
  knowledgeBaseName: string;
  question: string;
  result: KnowledgeAnswerResult;
};

export const ANSWER_EXPORT_FILENAME = 'knowledge-answer.txt';

export function buildAnswerText({ workspaceName, knowledgeBaseName, question, result }: AnswerTextExportInput): string {
  const lines = [
    '企业知识库问答记录',
    '',
    `工作空间：${workspaceName}`,
    `知识库：${knowledgeBaseName}`,
    '',
    '本次提交的问题：',
    question,
    '',
    `结果：${result.abstained ? '证据不足，本次弃答' : '已生成回答'}`,
    '回答：',
    result.answer,
    '',
    `引用来源：${result.citations.length} 条`,
  ];
  result.citations.forEach((citation, index) => lines.push(
    '',
    `引用 ${index + 1}`,
    `文档名称：${citation.documentName}`,
    `文档 ID：${citation.documentId}`,
    `文本块 ID：${citation.chunkId}`,
    `文本块序号：${citation.chunkIndex}`,
    `引文字符范围：[${citation.startChar}, ${citation.endChar})`,
    '完整引文：',
    citation.quote,
  ));
  lines.push(
    '',
    '说明：',
    '本文件保存本次已确认的回答和引文，不包含完整文档或来源段落预览。',
    '引文字符范围对应解析文本中的字符位置，不是文件字节位置。',
    '引用存在不保证回答正确，请结合原文上下文核对。',
    ...(result.abstained ? ['本次弃答仅表示本次检索到的证据不足，不能据此认定整个知识库没有相关资料。'] : []),
    '',
  );
  return lines.join('\r\n');
}

export function downloadAnswerText(input: AnswerTextExportInput): void {
  const blob = new Blob(['\uFEFF', buildAnswerText(input)], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  let anchor: HTMLAnchorElement | null = null;
  let requested = false;
  try {
    anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = ANSWER_EXPORT_FILENAME;
    anchor.hidden = true;
    document.body.append(anchor);
    anchor.click();
    requested = true;
  } finally {
    try {
      anchor?.remove();
    } finally {
      // Allow the browser to consume the URL before revoking a requested download.
      if (requested) window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      else URL.revokeObjectURL(url);
    }
  }
}
