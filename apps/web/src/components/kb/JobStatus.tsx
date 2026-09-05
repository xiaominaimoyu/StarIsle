import type { JobStatus as JobStatusType } from "@star-isle/contracts";

const statusLabel: Record<JobStatusType["status"], string> = {
  queued: "排队中",
  running: "运行中",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
};

const statusClass: Record<JobStatusType["status"], string> = {
  queued: "text-text-dim",
  running: "text-accent-warm",
  succeeded: "text-success",
  failed: "text-danger",
  cancelled: "text-text-dim",
};

function safeErrorCode(errorCode: string | null): string | null {
  if (!errorCode) return null;
  // 错误码是状态标识，不把可能包含后端详情的任意字符串直接带入状态区。
  return /^[A-Z0-9_-]{1,64}$/.test(errorCode) ? errorCode : "UNKNOWN";
}

/** JobStatus 五态配色:排队(dim)/运行中(暖金脉冲)/成功/失败+错误码/已取消 */
export default function JobStatus({ job }: { job: JobStatusType }) {
  const errorCode = safeErrorCode(job.errorCode);
  return (
    <span
      role="status"
      aria-live="polite"
      className={`inline-flex items-center gap-1.5 text-xs ${statusClass[job.status]}`}
    >
      {job.status === "running" ? <span className="heartbeat-dot" aria-hidden="true" /> : null}
      <span>
        {statusLabel[job.status]}
        {job.progress !== null ? <span className="ml-1">进度 {job.progress}</span> : null}
        {job.status === "failed" && errorCode ? <code className="ml-1 font-mono text-[0.7em] opacity-80">{errorCode}</code> : null}
      </span>
    </span>
  );
}
