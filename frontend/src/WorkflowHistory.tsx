import { Answer } from './KnowledgeWorkflow';
import { canReadSavedWorkflowAnswer, type WorkflowMeta, type WorkflowSavedAnswer } from './workflow-api';
import type { WorkflowHistoryItem } from './workflow-history-api';

export type HistoryReadNotice = { message: string; traceId: string | null };
export type WorkflowHistoryView = {
  items: WorkflowHistoryItem[] | null;
  listNotice: HistoryReadNotice | null;
  selected: WorkflowHistoryItem | null;
  snapshot: WorkflowMeta | null;
  readNotice: HistoryReadNotice | null;
  savedAnswer: WorkflowSavedAnswer | null;
  savedAnswerNotice: string;
};

type Props = {
  workspaceName: string;
  knowledgeBaseName: string;
  activity: 'idle' | 'listing' | 'reading' | 'answer-reading' | 'logout';
  view: WorkflowHistoryView;
  error: string;
  onList: () => void;
  onSelect: (item: WorkflowHistoryItem) => void;
  onRead: () => void;
  onReadAnswer: () => void;
  onCancel: () => void;
  onBack: () => void;
};

const STATUS_TEXT: Record<WorkflowMeta['status'], string> = {
  pending: '已创建，尚未执行',
  running: '执行中',
  failed: '执行失败',
  completed: '已完成',
};

const STEP_TEXT: Record<WorkflowMeta['nextStep'], string> = {
  retrieve: '检索资料', generate: '生成回答', done: '处理完成',
};

const ERROR_TEXT: Record<string, string> = {
  embedding_timeout: '问题向量服务等待超时',
  embedding_error: '问题向量服务失败',
  retrieval_conflict: '资料检索发生冲突',
  retrieval_invalid: '检索结果未通过校验',
  generation_configuration: '回答服务配置不可用',
  generation_timeout: '回答服务等待超时',
  generation_error: '回答服务失败',
  evidence_changed: '引用资料已变化',
  checkpoint_invalid: '保存的检查点未通过校验',
  context_limit: '正文超过当前任务预算',
  service_error: '服务处理失败',
  execution_interrupted: '执行中断',
};

function displayTime(value: string) {
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? '时间未识别' : time.toLocaleString('zh-CN', { hour12: false });
}

function ReadNotice({ notice }: { notice: HistoryReadNotice | null }) {
  if (!notice) return null;
  return <p className="message error" role="alert">{notice.message}
    {notice.traceId && <><br /><span>排错编号：<code>{notice.traceId}</code>（反馈问题时请提供此编号）</span></>}
  </p>;
}

function HistoryMetadata({ workflow }: { workflow: WorkflowMeta }) {
  return <>
    <p className="submitted-query">任务问题：<strong>{workflow.question}</strong></p>
    <p>记录状态：<strong>{STATUS_TEXT[workflow.status]}</strong>；当前步骤：{STEP_TEXT[workflow.nextStep]}。</p>
    <p>记录版本：{workflow.version}；已记录尝试次数：{workflow.attemptCount}。</p>
    <p>创建时冻结的正文字符预算：{workflow.maxContextCharacters} 个字符。</p>
    <p>{workflow.checkpointCount === null ? '尚无已保存的检索检查点。'
      : `检索检查点已保存 ${workflow.checkpointCount} 个文本块。`}</p>
    {workflow.errorCode !== null && <p className="message error">失败原因：
      {Object.hasOwn(ERROR_TEXT, workflow.errorCode)
        ? ERROR_TEXT[workflow.errorCode] : '原因未识别，未展示原始内容'}。</p>}
    <p className="session-note">这是本次读取时收到的记录，不是实时进度；记录版本不等于执行次数。
      本页只读，不执行、恢复或接管任务。</p>
  </>;
}

export default function WorkflowHistory({ workspaceName, knowledgeBaseName, activity,
  view, error, onList, onSelect, onRead, onReadAnswer, onCancel, onBack }: Props) {
  const busy = activity !== 'idle';
  const canReadAnswer = !busy && canReadSavedWorkflowAnswer(view.snapshot);

  return <section className="workflow-history" aria-labelledby="workflow-history-title">
    <button className="button secondary back-button" disabled={activity === 'logout'} onClick={onBack}>
      返回文档列表</button>
    <div className="page-heading"><div>
      <p className="eyebrow">工作空间：<strong>{workspaceName}</strong></p>
      <h1 id="workflow-history-title">工作流记录</h1>
      <p className="intro">知识库：<strong>{knowledgeBaseName}</strong></p>
    </div></div>
    {activity === 'logout' ? <p className="message" role="status">正在确认退出…</p> : <>
      {error && <p className="message error" role="alert">{error}</p>}
      <section className="search-form-panel" aria-labelledby="workflow-history-list-title"
        aria-busy={activity === 'listing'}>
        <h2 id="workflow-history-list-title">最近创建的工作流</h2>
        <p>读取当前知识库成员共享的任务记录，每次最多显示最近创建的 20 条。
          这里按创建时间排列，不代表最近更新的任务，也不是全部历史记录。</p>
        <p className="session-note">进入本页不会自动读取；读取列表、状态和已保存答案均为只读操作，不调用模型。</p>
        <div className="processing-actions">
          <button className="button primary" disabled={busy} onClick={onList}>读取最近工作流</button>
          {activity === 'listing' && <button className="button secondary" onClick={onCancel}>停止读取工作流记录</button>}
        </div>
        {activity === 'listing' && <p role="status">正在读取最近创建的工作流记录…</p>}
        <ReadNotice notice={view.listNotice} />
        {view.items === null ? <p>尚无本次有效列表读取记录。</p>
          : view.items.length === 0 ? <p className="empty-state">本次读取未发现工作流记录。</p>
            : <>
              <p className="page-caption">本次读取 · {view.items.length} 条工作流记录</p>
              <ul className="workspace-list">{view.items.map(item => <li className="workspace-card" key={item.id}>
                <div>
                  <h3>{item.question}</h3>
                  <p>记录状态：<strong>{STATUS_TEXT[item.status]}</strong></p>
                  <p>创建时间：<time dateTime={item.createdAt}>{displayTime(item.createdAt)}</time></p>
                  {item.strategy === 'vector' ? <button className="button secondary card-entry" disabled={busy}
                    aria-label={`查看工作流：${item.question}`} onClick={() => onSelect(item)}>
                    查看任务详情</button>
                    : <p className="session-note">本页暂不支持打开混合检索历史。</p>}
                </div>
              </li>)}</ul>
              {view.items.length === 20 && <p className="session-note">本次已读取 20 条，可能还有更早的记录；本版不提供翻页。</p>}
            </>}
      </section>
      {view.selected && <>
        <section className="conversation-snapshot" aria-labelledby="workflow-history-state-title"
          aria-busy={activity === 'reading'}>
          <h2 id="workflow-history-state-title">选中任务状态</h2>
          <p className="submitted-query">选中的问题：<strong>{view.selected.question}</strong></p>
          <div className="processing-actions">
            <button className="button secondary" disabled={busy} onClick={onRead}>重新读取选中任务状态</button>
            {activity === 'reading' && <button className="button secondary" onClick={onCancel}>停止读取任务状态</button>}
          </div>
          {activity === 'reading' && <p role="status">正在读取选中任务的当前状态…</p>}
          <ReadNotice notice={view.readNotice} />
          {view.snapshot ? <HistoryMetadata workflow={view.snapshot} /> : <p>尚无选中任务的有效状态读取记录。</p>}
          <p className="session-note">列表中的状态只是列表读取时的快照；详情以这次单独读取的结果为准。
            本页不自动轮询，也不根据失败状态发起恢复。</p>
        </section>
        <section className="answer-results" aria-label="历史任务已保存答案（只读）"
          aria-busy={activity === 'answer-reading'}>
          <h2>已保存答案（只读）</h2>
          <p>确认选中任务已完成后，可单独查看已保存的问题、答案和完整引用；全部选中来源核对通过才展示。</p>
          <div className="processing-actions">
            <button className="button secondary" disabled={!canReadAnswer}
              onClick={() => { if (canReadAnswer) onReadAnswer(); }}>
              {activity === 'answer-reading' ? '正在核对已保存答案…' : '查看已保存答案'}</button>
            {activity === 'answer-reading' && <button className="button secondary" onClick={onCancel}>停止查看答案</button>}
          </div>
          {activity === 'answer-reading' && <p role="status">正在读取已保存结果并核对全部选中来源的原文块…</p>}
          {view.savedAnswerNotice && <p className="message error" role="alert">{view.savedAnswerNotice}</p>}
          {view.savedAnswer && <>
            <HistoryMetadata workflow={view.savedAnswer.workflow} />
            <Answer answer={view.savedAnswer.answer} />
          </>}
          <p className="session-note">这里展示任务当前保存的记录，不能用来确认某次超时请求的执行回执。
            停止查看只取消浏览器读取与核对，不会删除答案。</p>
        </section>
      </>}
    </>}
  </section>;
}
