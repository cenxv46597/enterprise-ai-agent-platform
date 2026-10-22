import { ApiError, getDocument, getDocumentChunk, strictUuid } from './api';
import type { AnswerCitation, DocumentChunk } from './api';

const UNCONFIRMED = '无法确认这条引文的当前来源段落，请稍后手动重新读取。';

export class AnswerCitationSourceError extends ApiError {
  constructor(status: number | null = null, traceId: unknown = null) {
    super(UNCONFIRMED, status, traceId);
    this.name = 'AnswerCitationSourceError';
  }
}

function copyCitation(citation: AnswerCitation): AnswerCitation {
  if (!citation || typeof citation !== 'object' || Array.isArray(citation)) throw new AnswerCitationSourceError();
  const target = {
    chunkId: strictUuid(citation.chunkId), documentId: strictUuid(citation.documentId),
    documentName: citation.documentName, chunkIndex: citation.chunkIndex,
    quote: citation.quote, startChar: citation.startChar, endChar: citation.endChar,
  };
  if (typeof target.documentName !== 'string' || !target.documentName.trim()
      || Array.from(target.documentName).length > 255
      || !Number.isSafeInteger(target.chunkIndex) || target.chunkIndex < 1
      || typeof target.quote !== 'string' || !target.quote.trim()
      || Array.from(target.quote).length > 1000
      || !Number.isSafeInteger(target.startChar) || target.startChar < 0
      || !Number.isSafeInteger(target.endChar) || target.endChar <= target.startChar
      || target.endChar - target.startChar !== Array.from(target.quote).length) {
    throw new AnswerCitationSourceError();
  }
  return target;
}

export async function readAnswerCitationSource(workspaceId: string, knowledgeBaseId: string,
  citation: AnswerCitation, signal: AbortSignal): Promise<DocumentChunk> {
  try {
    const workspace = strictUuid(workspaceId), knowledgeBase = strictUuid(knowledgeBaseId);
    const target = copyCitation(citation);
    if (signal.aborted) throw new AnswerCitationSourceError();
    const document = await getDocument(workspace, knowledgeBase, target.documentId, signal);
    if (signal.aborted || document.originalFilename !== target.documentName || document.status !== 'ready') {
      throw new AnswerCitationSourceError();
    }
    const chunk = await getDocumentChunk(workspace, knowledgeBase, target.documentId, target.chunkId, signal);
    if (signal.aborted) throw new AnswerCitationSourceError();
    if (chunk.documentId !== target.documentId || chunk.chunkIndex !== target.chunkIndex
        || target.startChar < chunk.startChar || target.endChar > chunk.endChar
        || Array.from(chunk.content).slice(target.startChar - chunk.startChar,
          target.endChar - chunk.startChar).join('') !== target.quote) {
      throw new AnswerCitationSourceError();
    }
    // This checks one current citation location, not the whole answer or its original context snapshot.
    return chunk;
  } catch (failure) {
    if (signal.aborted) throw new AnswerCitationSourceError();
    if (failure instanceof ApiError && failure.status === 401) throw failure;
    throw new AnswerCitationSourceError(
      failure instanceof ApiError ? failure.status : null,
      failure instanceof ApiError && failure.status !== null ? failure.traceId : null,
    );
  }
}
