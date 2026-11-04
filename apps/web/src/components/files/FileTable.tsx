import { useCallback, useEffect, useRef, useState } from "react";
import type { FileListResponse, FileSummary } from "@star-isle/contracts";
import { deleteFile, downloadFileUrl, listFiles, uploadFile } from "@/lib/api/modules";
import { getCsrf } from "@/stores/session";
import { formatBytes, formatDateTime } from "@/lib/format";
import DropZone from "@/components/gallery/DropZone";
import Modal from "@/components/ui/Modal";
import EmptyState from "@/components/ui/EmptyState";
import StatusBanner from "@/components/ui/StatusBanner";

function uniqueFiles(files: FileSummary[]): FileSummary[] {
  const seen = new Set<string>();
  return files.filter((file) => {
    if (seen.has(file.id)) return false;
    seen.add(file.id);
    return true;
  });
}

/**
 * 文件表格:cursor 分页 + "加载更多"按钮(不做无限滚动,键盘/读屏可预期)。
 * 删除二次确认 + 失败回滚;上传复用 DropZone 与进度规范。
 */
export default function FileTable() {
  const [files, setFiles] = useState<FileSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<FileSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const mountedRef = useRef(false);
  const loadRevisionRef = useRef(0);
  const mutationRevisionRef = useRef(0);
  const deletedIdsRef = useRef(new Set<string>());
  const deletingRef = useRef(false);

  const load = useCallback(async (cursor?: string) => {
    if (!mountedRef.current) return;
    const revision = ++loadRevisionRef.current;
    const mutationAtStart = mutationRevisionRef.current;
    const append = cursor !== undefined;
    if (append) {
      setLoadingMore(true);
      setLoadMoreError(null);
    } else {
      setLoading(true);
      setLoadingMore(false);
      setLoadError(null);
      setLoadMoreError(null);
    }
    const current = () => mountedRef.current && revision === loadRevisionRef.current;
    try {
      const res: FileListResponse = await listFiles(cursor);
      if (!current()) return;
      setFiles((prev) => {
        if (!current()) return prev;
        const preserveMutations = mutationAtStart !== mutationRevisionRef.current;
        return uniqueFiles(append || preserveMutations ? [...prev, ...res.items] : res.items)
          .filter((file) => !deletedIdsRef.current.has(file.id));
      });
      setNextCursor(res.nextCursor);
    } catch {
      if (!current()) return;
      if (append) setLoadMoreError("更多文件加载失败,请重试。");
      else setLoadError("文件列表加载失败,请重试。");
    } finally {
      if (current()) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    return () => {
      mountedRef.current = false;
      loadRevisionRef.current += 1;
    };
  }, [load]);

  const confirmDelete = async () => {
    if (!mountedRef.current || !pendingDelete || deletingRef.current) return;
    const file = pendingDelete;
    deletingRef.current = true;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      const token = getCsrf();
      if (!token) throw new Error("安全验证不可用,请刷新页面后重试。");
      await deleteFile(file.id, token);
      if (!mountedRef.current) return;
      mutationRevisionRef.current += 1;
      deletedIdsRef.current.add(file.id);
      setFiles((prev) => prev.filter((item) => item.id !== file.id));
      setPendingDelete(null);
    } catch (err) {
      if (mountedRef.current) setDeleteError(err instanceof Error ? err.message : "删除失败,请重试。");
    } finally {
      deletingRef.current = false;
      if (mountedRef.current) setDeleteBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <DropZone
        maxBytes={100 * 1024 * 1024}
        label="拖放文件到此处,或点击选择(支持多选)"
        onUpload={async (file, _item, update) => {
          if (!mountedRef.current) return;
          const token = getCsrf();
          if (!token) throw new Error("安全验证不可用,请刷新页面后重试。");
          update({ progress: 15 });
          const summary = await uploadFile(file, token, crypto.randomUUID());
          if (!mountedRef.current) return;
          mutationRevisionRef.current += 1;
          update({ progress: 100 });
          setFiles((prev) => uniqueFiles([summary, ...prev]));
        }}
      />

      {loadError ? (
        <StatusBanner tone="error" action={<button type="button" onClick={() => void load()}>重试</button>}>
          {loadError}
        </StatusBanner>
      ) : null}
      {loading && files.length === 0 ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="skeleton h-12 rounded-sm" />
          ))}
        </div>
      ) : files.length === 0 ? (
        loadError ? null : <EmptyState
          title="暂无文件"
          description="拖放或点击上传第一个文件。文件仅保存在你的私有存储中。"
        />
      ) : (
        <>
          {/* 桌面表格 */}
          <div className="card hidden overflow-x-auto rounded-sm border-t-2 border-t-accent-warm/60 md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left font-mono text-xs tracking-wide text-text-dim">
                  <th className="px-4 py-3 font-medium">名称</th>
                  <th className="px-4 py-3 font-medium">大小</th>
                  <th className="px-4 py-3 font-medium">类型</th>
                  <th className="px-4 py-3 font-medium">上传时间</th>
                  <th className="px-4 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {files.map((f) => (
                  <tr key={f.id} className="border-b border-border/50 last:border-0 hover:bg-surface/60">
                    <td className="max-w-64 truncate px-4 py-3 text-text">{f.name}</td>
                    <td className="px-4 py-3 text-text-dim">{formatBytes(f.size)}</td>
                    <td className="px-4 py-3 text-text-dim">{f.mimeType}</td>
                    <td className="px-4 py-3 text-text-dim">{formatDateTime(f.createdAt)}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-2">
                        <a
                          href={downloadFileUrl(f.id)}
                          className="rounded text-accent hover:underline focus-ring"
                        >
                          下载
                        </a>
                        <button
                          type="button"
                          className="rounded text-danger hover:underline focus-ring"
                          onClick={() => { setDeleteError(null); setPendingDelete(f); }}
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 移动端卡片列表 */}
          <ul className="flex flex-col gap-2 md:hidden">
            {files.map((f) => (
              <li key={f.id} className="card rounded-sm border-l-2 border-l-accent-warm/60 p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-text">{f.name}</p>
                    <p className="mt-0.5 text-xs text-text-dim">
                      {formatBytes(f.size)} · {f.mimeType} · {formatDateTime(f.createdAt)}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-2 text-xs">
                    <a href={downloadFileUrl(f.id)} className="text-accent hover:underline focus-ring">
                      下载
                    </a>
                    <button type="button" className="text-danger hover:underline focus-ring" onClick={() => { setDeleteError(null); setPendingDelete(f); }}>
                      删除
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>

          {nextCursor ? (
            <div className="flex flex-col items-center gap-2">
              {loadMoreError ? <p role="status" className="text-sm text-danger">{loadMoreError}</p> : null}
              <button
                type="button"
                disabled={loading || loadingMore}
                aria-busy={loadingMore}
                className="mx-auto inline-flex h-11 items-center rounded-sm border border-border bg-surface/70 px-4 text-sm hover:border-accent-warm/70 focus-ring disabled:opacity-60"
                onClick={() => void load(nextCursor)}
              >
                {loadingMore ? "加载中…" : loadMoreError ? "重试加载更多" : "加载更多"}
              </button>
            </div>
          ) : null}
        </>
      )}

      <Modal
        open={!!pendingDelete}
        title="删除文件"
        variant="destructive"
        confirmLabel="确认删除"
        confirmLoading={deleteBusy}
        onClose={() => {
          if (!deleteBusy) {
            setDeleteError(null);
            setPendingDelete(null);
          }
        }}
        onConfirm={confirmDelete}
      >
        删除后将按保留策略清理,期间不可在列表中访问。确认删除?
        {deleteError ? (
          <p role="alert" className="mt-2 text-sm text-danger">{deleteError}</p>
        ) : null}
      </Modal>
    </div>
  );
}
