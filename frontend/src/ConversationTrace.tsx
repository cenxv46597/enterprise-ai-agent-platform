import type { ConversationTrace as Trace } from './conversation-trace';

const STATUS = { success: '成功', error: '错误', stopped: '已停止' };
const RUN_STATUS = { answered: '运行已完成', waiting: '等待补充条件', failed: '运行失败', stopped: '运行已停止' };
const milliseconds = (value: number) => `${value.toFixed(1)} 毫秒`;

export default function ConversationTrace({ trace, kind }: {
  trace: Trace; kind: keyof typeof RUN_STATUS;
}) {
  return <details className="conversation-trace">
    <summary>查看本轮处理过程</summary>
    <section aria-label="首条消息处理过程">
      <h3>首条消息处理过程（已保存）</h3>
      <p>首条消息运行状态：{RUN_STATUS[kind]}。以下属于首条消息，
        不包含后续补充和固定计费执行；步骤成功不等于答案正确或业务任务完成。</p>
      {trace.kind === 'unavailable' ? <p className="message">处理过程格式未确认，未展示步骤与统计；已核对的回复保留。</p> : <>
        {trace.reason && <p>运行结束原因：{trace.reason}。</p>}
        {trace.metrics ? <div className="session-note">
          <p>控制器运行耗时：{milliseconds(trace.metrics.totalMs)}；
            决策次数：{trace.metrics.decisionCount}；实际进入工具执行：{trace.metrics.toolExecutionCount} 次。</p>
          <p>决策包含程序决策，不等于模型请求数；工具执行故障仍可能计次。
            运行耗时不包含网页登录、网络传输或用户补充等待，不能据此推算费用。</p>
        </div> : <p className="session-note">该轮未记录运行统计；未补造耗时或执行次数。</p>}
        {trace.steps.length === 0 ? <p>该轮没有可展示的步骤记录，不能据此断言工具未执行。</p> :
          <ol className="conversation-trace-list">{trace.steps.map(step => <li key={step.number}>
            <h4>步骤 {step.number} · {step.name} · {STATUS[step.status]}</h4>
            {step.reason && <p>原因：{step.reason}。</p>}
            {step.execution === 'blocked' && <p>本步未进入工具执行。</p>}
            {step.execution === 'unknown' && <p>本步是否进入工具执行尚未确认，不能凭耗时推断。</p>}
            <p className="page-caption">动作决策耗时：{milliseconds(step.plannerMs)}；
              {step.action === 'finish' ? '结束处理' : step.action === 'tool' ? '工具阶段' : '后续阶段'}耗时：
              {step.executionMs === null ? '未记录' : milliseconds(step.executionMs)}。</p>
            {step.evidence.length > 0 && <ul>{step.evidence.map(source => <li key={source.evidenceId}>
              证据 {source.evidenceId}：{source.documentName} · 文本块 {source.chunkIndex}
              （字符范围 [{source.startChar}, {source.endChar})）
            </li>)}</ul>}
            {step.calculationId !== null && <p>关联算术记录 {step.calculationId}；本页未核对该记录的计算明细。</p>}
          </li>)}</ol>}
      </>}
      <p className="session-note">此处只展示公开处理记录，不是模型内部思考。展开或收起不会发送请求，也不会重跑工具。</p>
    </section>
  </details>;
}
