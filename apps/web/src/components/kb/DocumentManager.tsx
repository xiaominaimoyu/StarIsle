import { useCallback, useEffect, useRef, useState } from "react";
import type { JobStatus as JobStatusType, KnowledgeDocument } from "@star-isle/contracts";
import {
  deleteDocument,
  cancelJob,
  fetchJob,
  listDocuments,
  listJobs,
  rebuildDocument,
  uploadDocument,
} from "@/lib/api/modules";
import { getCsrf } from "@/stores/session";
import { formatDateTime } from "@/lib/format";
import JobStatus from "./JobStatus";
import Modal from "@/components/ui/Modal";
import EmptyState from "@/components/ui/EmptyState";
import StatusBanner from "@/components/ui/StatusBanner";
import { appendUniqueDocuments } from "./document-pagination";

const ACCEPTED_FILE_EXTENSIONS = ".pdf,.md,.markdown,.txt";
const MAX_POLL_ATTEMPTS = 10;

type JobOperation = "upload" | "rebuild" | "delete";

interface TrackedJob {
  documentId: string;
  jobId: string;
  operation: JobOperation;
  status: JobStatusType;
  pollError: string | null;
}

function isActive(status: JobStatusType): boolean {
  return status.status === "queued" || status.status === "running";
}

function initialJobStatus(): JobStatusType {
  return {
    status: "queued",
    progress: null,
    errorCode: null,
    updatedAt: new Date().toISOString(),
  };
}

function jobFromIndex(document: KnowledgeDocument): JobStatusType {
  return {
    status: document.indexState === "ready"
      ? "succeeded"
      : document.indexState === "failed"
        ? "failed"
        : document.indexState === "queued"
          ? "queued"
          : "running",
    progress: null,
    errorCode: document.indexErrorCode,
    updatedAt: document.createdAt,
  };
}

function mergeLoadedDocuments(
  loaded: KnowledgeDocument[],
  current: KnowledgeDocument[],
  tracked: Record<string, TrackedJob>,
): KnowledgeDocument[] {
  const next = appendUniqueDocuments([], loaded);
  const existing = new Set(next.map((document) => document.id));

  // 删除 accepted 后后端可能立即隐藏文档。本页保留 tombstone，不能借一次
  // list 刷新把它悄悄抹掉；刷新/重新登录后的持久化并不由此承诺。
  for (const document of current) {
    const job = tracked[document.id];
    if (existing.has(document.id) || !job) continue;
    if (job.operation === "delete" && job.status.status !== "succeeded") {
      next.push(document);
    }
  }
  return next;
}

/**
 * 文档管理:上传 / 删除 / 重建均体现异步 job 进度；每个文档只跟踪当前 job。
 * 轮询按 1s → 2s → 4s → 8s（封顶）执行，失败或超时保持最后真实状态并让用户手动重试查询。
 */
export default function DocumentManager() {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [trackedJobs, setTrackedJobs] = useState<Record<string, TrackedJob>>({});
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [jobLoadError, setJobLoadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<KnowledgeDocument | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [cancellingJobId, setCancellingJobId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mountedRef = useRef(false);
  const loadRevisionRef = useRef(0);
  const trackedJobsRef = useRef<Record<string, TrackedJob>>({});
  const pollTimers = useRef(new Map<string, number>());

  const updateTrackedJobs = useCallback(
    (updater: (current: Record<string, TrackedJob>) => Record<string, TrackedJob>) => {
      const next = updater(trackedJobsRef.current);
      trackedJobsRef.current = next;
      if (mountedRef.current) setTrackedJobs(next);
      return next;
    },
    [],
  );

  const clearPollTimer = useCallback((documentId: string) => {
    const timer = pollTimers.current.get(documentId);
    if (timer !== undefined) window.clearTimeout(timer);
    pollTimers.current.delete(documentId);
  }, []);

  const isCurrentJob = useCallback((documentId: string, jobId: string) => {
    return mountedRef.current && trackedJobsRef.current[documentId]?.jobId === jobId;
  }, []);

  const invalidateEarlierDocumentLoads = useCallback(() => {
    // accepted operation 是 list 结果的明确边界：此前发出的普通 load 不能再覆盖
    // 刚插入的本地行、tombstone 或错误状态。同步收起旧 spinner，旧 finally 会因 revision 不匹配失效。
    loadRevisionRef.current += 1;
    if (mountedRef.current) {
      setLoading(false);
      setLoadingMore(false);
      setLoadError(null);
      setLoadMoreError(null);
    }
  }, []);

  const load = useCallback(async (
    cursor?: string,
    source?: { documentId: string; jobId: string },
  ) => {
    const revision = ++loadRevisionRef.current;
    const isLoadMore = cursor !== undefined;
    if (mountedRef.current) {
      if (isLoadMore) {
        setLoadingMore(true);
        setLoadMoreError(null);
      } else {
        setLoading(true);
        setLoadingMore(false);
        setLoadError(null);
        setLoadMoreError(null);
      }
    }
    try {
      const response = await listDocuments(cursor);
      if (!mountedRef.current || revision !== loadRevisionRef.current) return;
      if (source && !isCurrentJob(source.documentId, source.jobId)) return;
      setDocs((current) => {
        if (!mountedRef.current || revision !== loadRevisionRef.current) return current;
        if (source && !isCurrentJob(source.documentId, source.jobId)) return current;
        return isLoadMore
          ? appendUniqueDocuments(current, response.items)
          : mergeLoadedDocuments(response.items, current, trackedJobsRef.current);
      });
      if (source && !isCurrentJob(source.documentId, source.jobId)) return;
      setNextCursor(response.nextCursor);
    } catch {
      if (!mountedRef.current || revision !== loadRevisionRef.current) return;
      if (source && !isCurrentJob(source.documentId, source.jobId)) return;
      if (isLoadMore) setLoadMoreError("更多文档加载失败,请重试。");
      else setLoadError("文档列表加载失败,请重试。");
    } finally {
      if (mountedRef.current && revision === loadRevisionRef.current) {
        if (isLoadMore) setLoadingMore(false);
        else setLoading(false);
      }
    }
  }, [isCurrentJob]);

  const finishTerminalJob = useCallback((documentId: string, jobId: string, job: JobStatusType) => {
    const current = trackedJobsRef.current[documentId];
    if (!current || current.jobId !== jobId) return;
    clearPollTimer(documentId);

    if (job.status === "succeeded" && current.operation === "delete") {
      updateTrackedJobs((tracked) => {
        const latest = tracked[documentId];
        if (!latest || latest.jobId !== jobId) return tracked;
        const { [documentId]: _removed, ...remaining } = tracked;
        return remaining;
      });
      if (mountedRef.current) {
        setDocs((documents) => documents.filter((document) => document.id !== documentId));
      }
    }
  }, [clearPollTimer, updateTrackedJobs]);

  const pollJob = useCallback((documentId: string, jobId: string, attempt = 0) => {
    clearPollTimer(documentId);
    const current = trackedJobsRef.current[documentId];
    if (!current || current.jobId !== jobId || !isActive(current.status)) return;

    const delay = Math.min(8000, 1000 * 2 ** attempt);
    const timer = window.setTimeout(() => {
      pollTimers.current.delete(documentId);
      const poll = async () => {
        if (!isCurrentJob(documentId, jobId)) return;
        try {
          const job = await fetchJob(jobId);
          if (!isCurrentJob(documentId, jobId)) return;

          const before = trackedJobsRef.current[documentId];
          if (!before || before.jobId !== jobId) return;
          updateTrackedJobs((tracked) => {
            const latest = tracked[documentId];
            if (!latest || latest.jobId !== jobId) return tracked;
            return { ...tracked, [documentId]: { ...latest, status: job, pollError: null } };
          });

          if (!isActive(job)) {
            finishTerminalJob(documentId, jobId, job);
            if (job.status === "succeeded" && before.operation !== "delete") {
              void load(undefined, { documentId, jobId });
            }
            return;
          }

          if (attempt + 1 >= MAX_POLL_ATTEMPTS) {
            updateTrackedJobs((tracked) => {
              const latest = tracked[documentId];
              if (!latest || latest.jobId !== jobId) return tracked;
              return {
                ...tracked,
                [documentId]: {
                  ...latest,
                  pollError: "状态查询未在预期时间内完成,已停止自动查询。",
                },
              };
            });
            return;
          }
          pollJob(documentId, jobId, attempt + 1);
        } catch {
          if (!isCurrentJob(documentId, jobId)) return;
          updateTrackedJobs((tracked) => {
            const latest = tracked[documentId];
            if (!latest || latest.jobId !== jobId) return tracked;
            return {
              ...tracked,
              [documentId]: {
                ...latest,
                pollError: "状态查询失败,已停止自动查询。",
              },
            };
          });
        }
      };
      void poll();
    }, delay);
    pollTimers.current.set(documentId, timer);
  }, [clearPollTimer, finishTerminalJob, isCurrentJob, load, updateTrackedJobs]);

  const startTracking = useCallback((documentId: string, jobId: string, operation: JobOperation) => {
    invalidateEarlierDocumentLoads();
    clearPollTimer(documentId);
    updateTrackedJobs((tracked) => ({
      ...tracked,
      [documentId]: {
        documentId,
        jobId,
        operation,
        status: initialJobStatus(),
        pollError: null,
      },
    }));
    pollJob(documentId, jobId);
  }, [clearPollTimer, invalidateEarlierDocumentLoads, pollJob, updateTrackedJobs]);

  const retryStatusQuery = useCallback((documentId: string) => {
    const tracked = trackedJobsRef.current[documentId];
    if (!tracked || !tracked.pollError || !isActive(tracked.status)) return;
    updateTrackedJobs((current) => {
      const latest = current[documentId];
      if (!latest || latest.jobId !== tracked.jobId) return current;
      return { ...current, [documentId]: { ...latest, pollError: null } };
    });
    pollJob(documentId, tracked.jobId);
  }, [pollJob, updateTrackedJobs]);

  const restoreActiveJobs = useCallback(async () => {
    setJobLoadError(null);
    try {
      const response = await listJobs(100, true);
      if (!mountedRef.current) return;
      const restored: Record<string, TrackedJob> = {};
      for (const job of response.items) {
        if (!job.documentId || restored[job.documentId]) continue;
        const operation = job.jobType === "ingest" ? "upload" : job.jobType === "rebuild" ? "rebuild" : null;
        if (!operation) continue;
        restored[job.documentId] = {
          documentId: job.documentId,
          jobId: job.jobId,
          operation,
          status: job,
          pollError: null,
        };
      }
      updateTrackedJobs((current) => {
        const next = { ...current };
        for (const [documentId, job] of Object.entries(restored)) {
          if (!next[documentId]) next[documentId] = job;
        }
        return next;
      });
      for (const job of Object.values(restored)) {
        if (trackedJobsRef.current[job.documentId]?.jobId === job.jobId) pollJob(job.documentId, job.jobId);
      }
    } catch {
      if (mountedRef.current) setJobLoadError("活动任务恢复失败；文档列表仍可正常使用。");
    }
  }, [pollJob, updateTrackedJobs]);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    void restoreActiveJobs();
    return () => {
      mountedRef.current = false;
      loadRevisionRef.current += 1;
      for (const timer of pollTimers.current.values()) window.clearTimeout(timer);
      pollTimers.current.clear();
    };
  }, [load, restoreActiveJobs]);

  const handleCancel = async (documentId: string) => {
    const tracked = trackedJobsRef.current[documentId];
    if (!tracked || !isActive(tracked.status) || tracked.operation === "delete") return;
    setCancellingJobId(tracked.jobId);
    setActionError(null);
    try {
      const token = getCsrf();
      if (!token) throw new Error("安全验证不可用,请刷新页面后重试。");
      const status = await cancelJob(tracked.jobId, token);
      if (!isCurrentJob(documentId, tracked.jobId)) return;
      updateTrackedJobs((current) => {
        const latest = current[documentId];
        if (!latest || latest.jobId !== tracked.jobId) return current;
        return { ...current, [documentId]: { ...latest, status, pollError: null } };
      });
      finishTerminalJob(documentId, tracked.jobId, status);
      void load(undefined, { documentId, jobId: tracked.jobId });
    } catch (error) {
      if (mountedRef.current) setActionError(error instanceof Error ? error.message : "取消任务失败,请重试。");
    } finally {
      if (mountedRef.current) setCancellingJobId(null);
    }
  };

  const handleUpload = async (file: File | undefined) => {
    if (!file || uploading) return;
    setUploading(true);
    setUploadError(null);
    setActionError(null);
    try {
      const token = getCsrf();
      if (!token) throw new Error("安全验证不可用,请刷新页面后重试。");
      const response = await uploadDocument(file, token, crypto.randomUUID());
      if (!mountedRef.current) return;
      setDocs((current) => {
        if (current.some((document) => document.id === response.documentId)) return current;
        return [
          ...current,
          {
            id: response.documentId,
            title: file.name,
            documentType: file.type === "application/pdf" ? "pdf" : file.type === "text/markdown" ? "markdown" : "txt",
            version: 1,
            hash: "",
            indexState: "queued",
            indexErrorCode: null,
            createdAt: new Date().toISOString(),
          },
        ];
      });
      startTracking(response.documentId, response.jobId, "upload");
    } catch (error) {
      if (mountedRef.current) {
        setUploadError(error instanceof Error ? error.message : "上传失败,请重试。");
      }
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
      if (mountedRef.current) setUploading(false);
    }
  };

  const handleRebuild = async (document: KnowledgeDocument) => {
    const existing = trackedJobsRef.current[document.id];
    if (existing && isActive(existing.status)) return;
    setActionError(null);
    try {
      const token = getCsrf();
      if (!token) throw new Error("安全验证不可用,请刷新页面后重试。");
      const response = await rebuildDocument(document.id, "手动重建", token);
      if (!mountedRef.current) return;
      setDocs((current) => current.map((item) => (
        item.id === document.id ? { ...item, indexState: "queued", indexErrorCode: null } : item
      )));
      startTracking(document.id, response.jobId, "rebuild");
    } catch (error) {
      if (mountedRef.current) {
        setActionError(error instanceof Error ? error.message : "重建任务提交失败,请重试。");
      }
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    const document = pendingDelete;
    const existing = trackedJobsRef.current[document.id];
    if (existing && isActive(existing.status)) return;
    setDeleteBusy(true);
    setActionError(null);
    try {
      const token = getCsrf();
      if (!token) throw new Error("安全验证不可用,请刷新页面后重试。");
      const response = await deleteDocument(document.id, token);
      if (!mountedRef.current) return;
      startTracking(document.id, response.jobId, "delete");
      setPendingDelete(null);
    } catch (error) {
      if (mountedRef.current) {
        setActionError(error instanceof Error ? error.message : "删除任务提交失败,请重试。");
      }
    } finally {
      if (mountedRef.current) setDeleteBusy(false);
    }
  };

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPTED_FILE_EXTENSIONS}
          className="hidden"
          onChange={(event) => void handleUpload(event.target.files?.[0])}
        />
        <button
          type="button"
          disabled={uploading}
          className="inline-flex h-11 items-center rounded-md bg-accent px-4 text-sm font-medium text-accent-foreground hover:brightness-110 focus-ring disabled:opacity-60"
          onClick={() => fileInputRef.current?.click()}
        >
          {uploading ? "上传中…" : "上传文档"}
        </button>
        <button type="button" className="inline-flex h-11 items-center rounded-md border border-border px-4 text-sm hover:border-accent/60 focus-ring" onClick={() => void load()}>
          刷新
        </button>
        <p className="text-xs text-text-dim">支持 PDF / Markdown / TXT;上传后异步索引,以任务状态为准。</p>
      </div>

      {uploadError ? <StatusBanner tone="error" live closable onClose={() => setUploadError(null)}>{uploadError}</StatusBanner> : null}
      {actionError ? <StatusBanner tone="error" live closable onClose={() => setActionError(null)}>{actionError}</StatusBanner> : null}
      {jobLoadError ? <StatusBanner tone="error" live action={<button type="button" onClick={() => void restoreActiveJobs()}>重试恢复</button>}>{jobLoadError}</StatusBanner> : null}

      {loading ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="skeleton h-14 rounded-md" />
          ))}
        </div>
      ) : loadError ? (
        <StatusBanner tone="error" live action={<button type="button" onClick={() => void load()}>重试</button>}>{loadError}</StatusBanner>
      ) : docs.length === 0 ? (
        <EmptyState
          title="知识库还是空的"
          description="上传 PDF、Markdown 或 TXT 文档,构建你的个人知识库。"
        />
      ) : (
        <>
        <ul className="flex flex-col gap-2">
          {docs.map((document) => {
            const tracked = trackedJobs[document.id];
            const active = Boolean(tracked && isActive(tracked.status));
            const deleteTombstone = tracked?.operation === "delete";
            const deleteTerminal = deleteTombstone && !isActive(tracked.status);
            const job = tracked?.status ?? jobFromIndex(document);
            return (
              <li key={document.id} className={`card flex flex-wrap items-center gap-3 p-3 ${deleteTombstone ? "opacity-80" : ""}`}>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-text">{document.title}</p>
                  <p className="mt-0.5 text-xs text-text-dim">
                    v{document.version} · {document.documentType} · {formatDateTime(document.createdAt)}
                  </p>
                  {deleteTombstone ? (
                    <p className="mt-1 text-xs text-text-dim">
                      {deleteTerminal
                        ? "删除任务未完成。后端可能已隐藏文档；当前 API 无法再次删除或恢复。"
                        : "删除任务已提交；本页暂时保留此文档，任务成功后才会移除。"}
                    </p>
                  ) : null}
                  {tracked?.pollError ? (
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-danger" role="status" aria-live="polite">
                      <span>{tracked.pollError}</span>
                      <button
                        type="button"
                        className="underline focus-ring"
                        onClick={() => retryStatusQuery(document.id)}
                      >
                        重试状态查询
                      </button>
                    </div>
                  ) : null}
                </div>
                <JobStatus job={job} />
                {!deleteTombstone ? (
                  <div className="flex gap-2 text-xs">
                    {active && tracked?.operation !== "delete" ? (
                      <button
                        type="button"
                        disabled={cancellingJobId === tracked.jobId}
                        className="inline-flex h-11 items-center rounded px-2 text-danger hover:underline focus-ring disabled:cursor-not-allowed disabled:opacity-50"
                        onClick={() => void handleCancel(document.id)}
                      >
                        {cancellingJobId === tracked.jobId ? "取消中…" : "取消任务"}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      disabled={active}
                      className="inline-flex h-11 items-center rounded px-2 text-accent hover:underline focus-ring disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={() => void handleRebuild(document)}
                    >
                      重建
                    </button>
                    <button
                      type="button"
                      disabled={active}
                      className="inline-flex h-11 items-center rounded px-2 text-danger hover:underline focus-ring disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={() => setPendingDelete(document)}
                    >
                      删除
                    </button>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
        {nextCursor ? (
          <div className="flex flex-col items-center gap-2">
            {loadMoreError ? <p role="status" className="text-sm text-danger">{loadMoreError}</p> : null}
            <button
              type="button"
              disabled={loadingMore}
              aria-busy={loadingMore}
              className="inline-flex h-11 items-center rounded-md border border-border px-4 text-sm hover:border-accent/60 focus-ring disabled:cursor-not-allowed disabled:opacity-60"
              onClick={() => void load(nextCursor)}
            >
              {loadingMore ? "正在加载更多…" : loadMoreError ? "重试加载更多" : "加载更多"}
            </button>
          </div>
        ) : null}
        </>
      )}

      <Modal
        open={!!pendingDelete}
        title="删除文档"
        variant="destructive"
        confirmLabel="确认删除"
        confirmLoading={deleteBusy}
        onClose={() => {
          if (!deleteBusy) setPendingDelete(null);
        }}
        onConfirm={confirmDelete}
      >
        删除后相关向量与引用将按保留策略清理。确认删除?
      </Modal>
    </section>
  );
}
