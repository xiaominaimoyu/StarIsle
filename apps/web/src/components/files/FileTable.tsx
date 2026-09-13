import { useCallback, useEffect, useState } from "react";
import type { FileListResponse, FileSummary } from "@star-isle/contracts";
import { deleteFile, downloadFileUrl, listFiles, uploadFile } from "@/lib/api/modules";
import { getCsrf } from "@/stores/session";
import { formatBytes, formatDateTime } from "@/lib/format";
import DropZone from "@/components/gallery/DropZone";
import Modal from "@/components/ui/Modal";
import EmptyState from "@/components/ui/EmptyState";
import StatusBanner from "@/components/ui/StatusBanner";

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
  const [pendingDelete, setPendingDelete] = useState<FileSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const load = useCallback(async (cursor?: string) => {
    if (cursor) setLoadingMore(true);
    else setLoading(true);
    setLoadError(null);
    try {
      const res: FileListResponse = await listFiles(cursor);
      setFiles((prev) => (cursor ? [...prev, ...res.items] : res.items));
      setNextCursor(res.nextCursor);
    } catch {
      setLoadError("文件列表加载失败,请重试。");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      const token = getCsrf();
      if (!token) throw new Error("安全验证不可用,请刷新页面后重试。");
      await deleteFile(pendingDelete.id, token);
      setFiles((prev) => prev.filter((f) => f.id !== pendingDelete.id));
      setPendingDelete(null);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "删除失败,请重试。");
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <DropZone
        maxBytes={100 * 1024 * 1024}
        label="拖放文件到此处,或点击选择(支持多选)"
        onUpload={async (file, _item, update) => {
          const token = getCsrf();
          if (!token) throw new Error("安全验证不可用,请刷新页面后重试。");
          update({ progress: 15 });
          const summary = await uploadFile(file, token, crypto.randomUUID());
          update({ progress: 100 });
          setFiles((prev) => [summary, ...prev]);
        }}
      />

      {loading ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="skeleton h-12 rounded-sm" />
          ))}
        </div>
      ) : loadError ? (
        <StatusBanner tone="error" action={<button type="button" onClick={() => load()}>重试</button>}>
          {loadError}
        </StatusBanner>
      ) : files.length === 0 ? (
        <EmptyState
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
                          onClick={() => setPendingDelete(f)}
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
                    <button type="button" className="text-danger hover:underline focus-ring" onClick={() => setPendingDelete(f)}>
                      删除
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>

          {nextCursor ? (
            <button
              type="button"
              disabled={loadingMore}
              className="mx-auto inline-flex h-10 items-center rounded-sm border border-border bg-surface/70 px-4 text-sm hover:border-accent-warm/70 focus-ring disabled:opacity-60"
              onClick={() => load(nextCursor)}
            >
              {loadingMore ? "加载中…" : "加载更多"}
            </button>
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
          if (!deleteBusy) setPendingDelete(null);
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
