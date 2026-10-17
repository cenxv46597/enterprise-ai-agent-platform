import { useState } from 'react';
import { formatBillingMoney, type BillingEstimate } from './conversation-api';

export function BillingConfirmation({ disabled, onExecute }: { disabled: boolean; onExecute: () => void }) {
  const [confirmed, setConfirmed] = useState(false);
  return <div className="conversation-billing-confirmation">
    <h3>确认规则适用，再执行</h3>
    <p>请核对上方规则、次数和月份属于同一计费期间。本次仅按固定公式估算，
      不覆盖优惠、套餐、税费或其他条款，不是实际账单，不会扣款。</p>
    <label><input type="checkbox" checked={confirmed} disabled={disabled}
      onChange={event => setConfirmed(event.currentTarget.checked)} />
      确认该规则适用于上述同一计费期间</label>
    <button className="button primary" disabled={disabled || !confirmed} onClick={() => {
      if (confirmed && !disabled) onExecute();
    }}>计算标准规则估算</button>
    <p className="session-note">勾选只表示你确认规则适用，点击计算后才会提交；结果未确认时不会自动再次执行。</p>
  </div>;
}

export function BillingResult({ result }: { result: BillingEstimate }) {
  return <section className="answer-results" aria-label="标准规则估算结果">
    <h2>标准规则估算已完成</h2>
    <p>规则原文：</p><blockquote className="answer-quote">{result.plan.ruleQuote}</blockquote>
    <p>原文费率：月费 {result.plan.monthlyFee.value} 元/个月；
      每次上门服务费 {result.plan.visitFee.value} 元/次。</p>
    <p>用户补充并已保存：同一计费期间 {result.months.value} 个月、上门 {result.visits.value} 次。</p>
    <h3>程序计算明细</h3>
    <ol>
      <li>月费小计：{result.plan.monthlyFee.value} 元/个月 × {result.months.value} 个月
        = {formatBillingMoney(result.monthlySubtotal)} 元</li>
      <li>上门服务小计：{result.plan.visitFee.value} 元/次 × {result.visits.value} 次
        = {formatBillingMoney(result.visitSubtotal)} 元</li>
      <li>合计：{formatBillingMoney(result.monthlySubtotal)} 元 + {formatBillingMoney(result.visitSubtotal)} 元
        = {formatBillingMoney(result.total)} 元</li>
    </ol>
    <p className="message">标准规则估算合计：{formatBillingMoney(result.total)} 元。</p>
    <p className="session-note">规则来源：{result.plan.monthlyFee.documentName} · 文本块 {result.plan.monthlyFee.chunkIndex}。
      合计由程序计算，并非原文直接给出的金额。仅为你确认适用的标准规则估算，
      不覆盖优惠、套餐、税费或其他条款，不是实际账单，未产生扣款。</p>
    <p>已保存轮次：{result.version}。修改条件需后续另建任务，本页不会再次执行。</p>
  </section>;
}
