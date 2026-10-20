export type WorkflowReadNotice = { message: string; traceId: string | null };

// Keep each read's safe message and diagnostic ID together in its own region.
export function ReadNotice({ notice }: { notice: WorkflowReadNotice | null }) {
  if (!notice) return null;
  return <p className="message error" role="alert">{notice.message}
    {notice.traceId && <><br /><span>排错编号：<code>{notice.traceId}</code>（反馈问题时请提供此编号）</span></>}
  </p>;
}
