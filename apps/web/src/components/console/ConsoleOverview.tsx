import { useCallback, useEffect, useState } from "react";
import type { JobListResponse, MailSummary, PhotoSummary, StorageStats } from "@star-isle/contracts";
import { fetchStorageStats, listJobs, listMail, listPhotos } from "@/lib/api/modules";
import { formatBytes, formatRelativeTime } from "@/lib/format";
import StatusBanner from "@/components/ui/StatusBanner";

/**
 * 概览四卡:存储用量 / 最近照片 / 最新邮件 / 作业摘要(有数据才显示)。
 * 任何一卡失败只影响该卡,显示卡内错误与重试。
 */
export default function ConsoleOverview() {
  return (
    <div className="console-overview">
      <StorageCard />
      <PhotosCard />
      <MailCard />
      <JobsCard />
    </div>
  );
}

function CardShell({
  title,
  href,
  error,
  onRetry,
  children,
}: {
  title: string;
  href: string;
  error: string | null;
  onRetry: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className="console-overview-card">
      <div className="console-overview-card__head">
        <h2 className="font-display text-base font-semibold text-text">{title}</h2>
        <a href={href} className="inline-flex min-h-11 items-center text-xs text-accent hover:underline focus-ring">查看全部 →</a>
      </div>
      {error ? (
        <StatusBanner tone="error" action={<button type="button" onClick={onRetry}>重试</button>}>
          {error}
        </StatusBanner>
      ) : (
        children
      )}
    </section>
  );
}

function StorageCard() {
  const [stats, setStats] = useState<StorageStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchStorageStats()
      .then(setStats)
      .catch(() => setError("存储用量加载失败。"));
  }, []);

  useEffect(load, [load]);
  return (
    <CardShell title="存储用量" href="/console/files" error={error} onRetry={load}>
      <p className="font-mono text-2xl font-semibold text-text">{stats ? formatBytes(stats.usedBytes) : "加载中…"}</p>
      <p className="text-xs text-text-dim">
        {stats ? `文件 ${stats.fileCount} 个 · 照片 ${stats.photoCount} 张` : "正在读取文件与照片占用"}
      </p>
    </CardShell>
  );
}

function PhotosCard() {
  const [photos, setPhotos] = useState<PhotoSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    listPhotos()
      .then((res) => setPhotos(res.items))
      .catch(() => setError("最近照片加载失败。"));
  }, []);

  useEffect(load, [load]);

  return (
    <CardShell title="最近照片" href="/console/gallery" error={error} onRetry={load}>
      {photos.length === 0 ? (
        <p className="text-sm text-text-dim">还没有照片。</p>
      ) : (
        <div className="grid grid-cols-3 gap-1.5" aria-label={`最近 ${Math.min(photos.length, 6)} 张照片`}>
          {photos.slice(0, 6).map((p) => (
            <img key={p.id} src={p.thumbnailUrl} alt="" loading="lazy" className="aspect-square rounded-md object-cover" />
          ))}
        </div>
      )}
    </CardShell>
  );
}

function MailCard() {
  const [mails, setMails] = useState<MailSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    listMail()
      .then((res) => setMails(res.items))
      .catch(() => setError("最新邮件加载失败。"));
  }, []);

  useEffect(load, [load]);

  return (
    <CardShell title="最新邮件" href="/console/mail" error={error} onRetry={load}>
      {mails.length === 0 ? (
        <p className="text-sm text-text-dim">暂无邮件。</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {mails.slice(0, 3).map((m) => (
            <li key={m.id} className="truncate text-sm">
              <span className={m.read ? "text-text-dim" : "font-semibold text-text"}>
                {!m.read ? <span className="text-accent" aria-label="未读">● </span> : null}
                {m.subject || "(无主题)"}
              </span>
              <span className="ml-1 text-xs text-text-dim">{formatRelativeTime(m.receivedAt)}</span>
            </li>
          ))}
        </ul>
      )}
    </CardShell>
  );
}

function JobsCard() {
  const [jobs, setJobs] = useState<JobListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null);
    listJobs(3)
      .then(setJobs)
      .catch(() => setError("作业摘要加载失败。"));
  }, []);

  useEffect(load, [load]);
  return (
    <CardShell title="作业摘要" href="/console/kb" error={error} onRetry={load}>
      {!jobs ? (
        <p className="text-sm text-text-dim">正在读取作业状态</p>
      ) : jobs.items.length === 0 ? (
        <p className="text-sm text-text-dim">暂无知识库作业。</p>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-text-dim">
            排队 {jobs.counts.queued} · 运行 {jobs.counts.running} · 失败 {jobs.counts.failed} · 已取消 {jobs.counts.cancelled}
          </p>
          <ul className="flex flex-col gap-1 text-xs text-text-dim">
            {jobs.items.map((job) => (
              <li key={job.jobId} className="truncate">{job.jobType} · {job.status}</li>
            ))}
          </ul>
        </div>
      )}
    </CardShell>
  );
}
