import { Answer } from './KnowledgeWorkflow';
import { canReadSavedWorkflowAnswer, type WorkflowMeta, type WorkflowSavedAnswer } from './workflow-api';
import { isWorkflowHistoryOffset, isWorkflowHistoryStatusFilter, WORKFLOW_HISTORY_PAGE_SIZE,
  type WorkflowHistoryItem, type WorkflowHistoryStatusFilter } from './workflow-history-api';
import { ReadNotice, type WorkflowReadNotice } from './WorkflowReadNotice';

export type HistoryReadNotice = WorkflowReadNotice;
export type WorkflowHistoryView = {
  statusFilter: WorkflowHistoryStatusFilter;
  offset: number;
  items: WorkflowHistoryItem[] | null;
  listNotice: HistoryReadNotice | null;
  selected: WorkflowHistoryItem | null;
  snapshot: WorkflowMeta | null;
  readNotice: HistoryReadNotice | null;
  savedAnswer: WorkflowSavedAnswer | null;
  savedAnswerNotice: HistoryReadNotice | null;
};

type Props = {
  workspaceName: string;
  knowledgeBaseName: string;
  activity: 'idle' | 'listing' | 'reading' | 'answer-reading' | 'logout';
  view: WorkflowHistoryView;
  error: string;
  onList: (offset: number) => void;
  onFilterChange: (statusFilter: WorkflowHistoryStatusFilter) => void;
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
  view, error, onList, onFilterChange, onSelect, onRead, onReadAnswer, onCancel, onBack }: Props) {
  const busy = activity !== 'idle';
  const canReadAnswer = !busy && canReadSavedWorkflowAnswer(view.snapshot);
  const canGoBack = view.offset >= WORKFLOW_HISTORY_PAGE_SIZE;
  const canGoNext = view.items?.length === WORKFLOW_HISTORY_PAGE_SIZE
    && isWorkflowHistoryOffset(view.offset + WORKFLOW_HISTORY_PAGE_SIZE);

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
        <h2 id="workflow-history-list-title">工作流历史列表</h2>
        <p>读取当前知识库成员共享的任务记录，按创建时间从新到旧排列，每页最多 {WORKFLOW_HISTORY_PAGE_SIZE} 条。
          这里按创建时间排列，不代表最近更新的任务。</p>
        <p className="session-note">进入本页不会自动读取；读取列表、状态和已保存答案均为只读操作，不调用模型。</p>
        <p className="session-note">浏览期间若有成员新增或删除记录，跨页可能出现重复或遗漏；任务状态变化也可能改变筛选结果。
          可读取最近工作流重新浏览。</p>
        <label htmlFor="workflow-history-status">任务状态</label>
        <select id="workflow-history-status" value={view.statusFilter} disabled={busy}
          onChange={event => {
            if (!busy && isWorkflowHistoryStatusFilter(event.target.value)) onFilterChange(event.target.value);
          }}>
          <option value="all">全部状态</option>
          <option value="pending">已创建，尚未执行</option>
          <option value="running">执行中</option>
          <option value="failed">执行失败</option>
          <option value="completed">已完成</option>
        </select>
        <p className="session-note">切换状态会回到第一页并清空旧内容，请手动读取；“读取最近工作流”按当前筛选条件从第一页读取。</p>
        <p className="session-note">筛选状态：{view.statusFilter === 'all' ? '全部状态' : STATUS_TEXT[view.statusFilter]}。</p>
        <nav className="processing-actions" aria-label="工作流列表翻页">
          <button className="button primary" disabled={busy} onClick={() => onList(0)}>读取最近工作流</button>
          <button className="button secondary" disabled={busy || !canGoBack}
            onClick={() => { if (!busy && canGoBack) onList(view.offset - WORKFLOW_HISTORY_PAGE_SIZE); }}>上一页</button>
          <button className="button secondary" disabled={busy || !canGoNext}
            onClick={() => { if (!busy && canGoNext) onList(view.offset + WORKFLOW_HISTORY_PAGE_SIZE); }}>下一页</button>
          {view.offset > 0 && <button className="button secondary" disabled={busy}
            onClick={() => onList(view.offset)}>重新读取本页</button>}
          {activity === 'listing' && <button className="button secondary" onClick={onCancel}>停止读取工作流记录</button>}
        </nav>
        <p className="page-caption">当前页：第 {view.offset / WORKFLOW_HISTORY_PAGE_SIZE + 1} 页，每页最多 {WORKFLOW_HISTORY_PAGE_SIZE} 条。</p>
        {activity === 'listing' && <p role="status">正在读取当前页的工作流记录…</p>}
        <ReadNotice notice={view.listNotice} />
        {view.items === null ? <p>尚无本次有效列表读取记录。</p>
          : view.items.length === 0 ? <p className="empty-state">本次读取未发现工作流记录。
            仅代表当前筛选条件下的本页结果。
            {view.offset > 0 && '可返回上一页或读取最近工作流。'}</p>
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
              {view.items.length === WORKFLOW_HISTORY_PAGE_SIZE && <p className="session-note">本页已读取 {WORKFLOW_HISTORY_PAGE_SIZE} 条，可能还有更早的记录；下一页也可能为空。</p>}
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
          <ReadNotice notice={view.savedAnswerNotice} />
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
