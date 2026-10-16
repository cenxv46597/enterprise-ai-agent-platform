import { useState } from 'react';
import type { FormEvent } from 'react';
import type { DocumentChunk, DocumentEmbedding as EmbeddingResult, KnowledgeDocument } from './api';

export type EmbeddingOutcome =
  | { kind: 'confirmed'; result: EmbeddingResult }
  | { kind: 'unconfirmed' | 'rejected'; message: string };

type Props = {
  document: KnowledgeDocument | null;
  chunks: DocumentChunk[] | null;
  activity: 'idle' | 'embedding' | 'blocked';
  outcome: EmbeddingOutcome | null;
  onEmbed: (maxChunks: number) => void;
  onCancel: () => void;
};

export default function DocumentEmbedding({ document, chunks, activity, outcome, onEmbed, onCancel }: Props) {
  const [budget, setBudget] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submittedBudget, setSubmittedBudget] = useState<number | null>(null);
  const busy = activity !== 'idle';
  const confirmedChunks = chunks !== null && chunks.length > 0;
  const allowedStatus = document?.status === 'parsed' || document?.status === 'ready';
  const canEmbed = !busy && allowedStatus && confirmedChunks;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canEmbed) return;
    if (!/^[0-9]+$/.test(budget) || !Number.isSafeInteger(Number(budget))
      || Number(budget) < 1 || Number(budget) > 1000) {
      setValidationError('请填写 1 到 1000 之间的整数，作为本次新生成块数上限。');
      return;
    }
    const maxChunks = Number(budget);
    if (document?.status === 'parsed' && chunks !== null && maxChunks < chunks.length) {
      setValidationError(`当前文档共有 ${chunks.length} 个文本块，超过本次上限 ${maxChunks} 块；`
        + '整份文档不会执行向量化，不会只处理前 N 块。请核对后重新填写上限。');
      return;
    }
    setValidationError(null);
    setSubmittedBudget(maxChunks);
    setBudget('');
    onEmbed(maxChunks);
  }

  return <section className="embedding-panel" aria-labelledby="embedding-title">
    <h2 id="embedding-title">文档向量化</h2>
    <p>将已有正文块转换为每块 1024 维的数字表示，不会重新切块。</p>
    <p className="session-note" id="embedding-budget-help">
      上限约束本次新生成的块数，不是金额或 Token，也不是跨请求的累计费用上限。
      后端会独立检查上限；复用结果或请求失败都不能直接认定零费用。
    </p>
    {chunks !== null && <p className="page-caption">当前已确认 {chunks.length} 个文本块</p>}
    {(!allowedStatus || !confirmedChunks) && <p className="message">
      需要确认文档为已解析或可检索状态，并确认已有非空文本块后，才能生成向量。
      请先刷新文档与切块，必要时完成切块。
    </p>}
    <form className="embedding-form" noValidate onSubmit={submit}>
      <label htmlFor="embedding-max-chunks">本次新生成块数上限</label>
      <input id="embedding-max-chunks" type="number" min="1" max="1000" step="1" required
        value={budget} disabled={!canEmbed} aria-describedby="embedding-budget-help"
        aria-invalid={validationError ? true : undefined}
        onChange={event => {
          setBudget(event.currentTarget.value);
          setValidationError(null);
        }} />
      <p className="session-note">请手动填写本次上限，点击“生成向量”才会发送请求。
        已解析文档若超过上限，会整份拒绝，不会只处理前 N 块。</p>
      {validationError && <p className="message error" role="alert">{validationError}</p>}
      <div className="processing-actions">
        <button className="button primary" type="submit" disabled={!canEmbed || budget === ''}>
          {activity === 'embedding' ? '正在向量化…' : '生成向量'}
        </button>
        {activity === 'embedding' && <button className="button secondary" type="button"
          onClick={onCancel}>停止等待向量化结果</button>}
      </div>
    </form>
    {activity === 'embedding' && <div>
      <p className="message" role="status">正在向量化…
        {submittedBudget !== null && ` 本次上限 ${submittedBudget} 块`}</p>
      <p className="session-note">停止等待只影响浏览器等待，不能保证后端停止处理或没有产生费用。
        请刷新文档与切块核对结果。</p>
    </div>}
    {outcome && <section className="embedding-result" aria-labelledby="embedding-result-title">
      <h2 id="embedding-result-title">本次向量化结果</h2>
      {outcome.kind === 'confirmed' ? <div role="status">
        <p>向量化请求成功，返回 {outcome.result.chunkCount} 块、每块 {outcome.result.dimensions} 维的结果。</p>
        <p>模型：<strong>{outcome.result.model}</strong></p>
        <p>{outcome.result.reused ? '复用已有结果' : '向量结果已保存'}；接口返回状态：可检索。</p>
        <p className="session-note">本卡片保留本次请求已确认的结果；文档最新状态以另行读取结果为准。</p>
      </div> : <div>
        <p className="message error" role="alert">{outcome.message}</p>
        {outcome.kind === 'unconfirmed' && <p className="session-note">
          请先使用“刷新文档与切块”核对已有结果；这里不会自动重试向量化。
          如仍需执行，请核对后重新填写本次上限。
        </p>}
      </div>}
    </section>}
  </section>;
}
