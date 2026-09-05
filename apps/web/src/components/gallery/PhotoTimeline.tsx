import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PhotoListResponse, PhotoSummary } from "@star-isle/contracts";
import { deletePhoto, listPhotos, uploadPhoto } from "@/lib/api/modules";
import { getCsrf } from "@/stores/session";
import DropZone from "./DropZone";
import Modal from "@/components/ui/Modal";
import EmptyState from "@/components/ui/EmptyState";
import StatusBanner from "@/components/ui/StatusBanner";
import {
  appendUniquePhotos,
  groupPhotosByCaptureDay,
  photoCaptureDayLabel,
} from "./photo-timeline-state";

/**
 * 相册时间轴:按 EXIF capture_at 分组(无 EXIF 归入"未知日期"),GPS 不在 UI 展示。
 * cursor 分页 + "加载更多"按钮(不做无限滚动,键盘/读屏可预期)。
 * 删除走 Modal 二次确认;删除失败时条目保持原位并显示错误。
 */
export default function PhotoTimeline() {
  const [photos, setPhotos] = useState<PhotoSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<PhotoSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const mountedRef = useRef(false);
  const loadRevisionRef = useRef(0);
  const refreshTimersRef = useRef(new Set<number>());

  const load = useCallback(async (cursor?: string) => {
    if (!mountedRef.current) return;
    const revision = ++loadRevisionRef.current;
    const isLoadMore = cursor !== undefined;
    if (isLoadMore) {
      setLoadingMore(true);
      setLoadMoreError(null);
    } else {
      setLoading(true);
      setLoadingMore(false);
      setLoadError(null);
      setLoadMoreError(null);
    }
    try {
      const res: PhotoListResponse = await listPhotos(cursor);
      if (!mountedRef.current || revision !== loadRevisionRef.current) return;
      setPhotos((prev) => {
        if (!mountedRef.current || revision !== loadRevisionRef.current) return prev;
        return isLoadMore ? appendUniquePhotos(prev, res.items) : res.items;
      });
      setNextCursor(res.nextCursor);
    } catch {
      if (!mountedRef.current || revision !== loadRevisionRef.current) return;
      if (isLoadMore) setLoadMoreError("更多照片加载失败,请重试。");
      else setLoadError("相册加载失败,请重试。");
    } finally {
      if (mountedRef.current && revision === loadRevisionRef.current) {
        if (isLoadMore) setLoadingMore(false);
        else setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    return () => {
      mountedRef.current = false;
      loadRevisionRef.current += 1;
      for (const timer of refreshTimersRef.current) window.clearTimeout(timer);
      refreshTimersRef.current.clear();
    };
  }, [load]);

  const groups = useMemo(() => groupPhotosByCaptureDay(photos), [photos]);

  const scheduleRefresh = useCallback(() => {
    if (!mountedRef.current) return;
    const timer = window.setTimeout(() => {
      refreshTimersRef.current.delete(timer);
      if (mountedRef.current) void load();
    }, 3000);
    refreshTimersRef.current.add(timer);
  }, [load]);

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    const photo = pendingDelete;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      const token = getCsrf();
      if (!token) throw new Error("安全验证不可用,请刷新页面后重试。");
      await deletePhoto(photo.id, token);
      loadRevisionRef.current += 1;
      if (!mountedRef.current) return;
      setLoading(false);
      setLoadingMore(false);
      setLoadError(null);
      setLoadMoreError(null);
      setPhotos((prev) => prev.filter((item) => item.id !== photo.id));
      setPendingDelete(null);
    } catch (err) {
      if (mountedRef.current) {
        setDeleteError(err instanceof Error ? err.message : "删除失败,请重试。");
      }
    } finally {
      if (mountedRef.current) setDeleteBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <StatusBanner tone="info" closable>
        GPS 信息默认不展示;照片仅保存在你的设备与服务器私有存储中。
      </StatusBanner>

      <DropZone
        accept="image/jpeg,image/png,image/webp,image/gif"
        maxBytes={20 * 1024 * 1024}
        label="拖放照片到此处,或点击选择(支持多选)"
        onUpload={async (file, _item, update) => {
          const token = getCsrf();
          if (!token) throw new Error("安全验证不可用,请刷新页面后重试。");
          update({ progress: 20 });
          const res = await uploadPhoto(file, token, crypto.randomUUID());
          update({ progress: res.thumbnailState === "ready" ? 100 : 80 });
          if (res.status === "ready") {
            await load();
          } else {
            // 缩略图异步处理:稍后刷新
            scheduleRefresh();
          }
        }}
      />

      {loading ? (
        <div className="grid grid-cols-3 gap-3 md:grid-cols-4 lg:grid-cols-6" aria-busy="true">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="skeleton aspect-square rounded-md" />
          ))}
        </div>
      ) : loadError ? (
        <StatusBanner tone="error" action={<button type="button" onClick={() => load()}>重试</button>}>
          {loadError}
        </StatusBanner>
      ) : photos.length === 0 ? (
        <EmptyState
          title="还没有照片"
          description="拖放或点击上传,开始你的时间轴。"
        />
      ) : (
        <>
          {groups.map(([label, items]) => (
            <section key={label} aria-label={label}>
              <h2 className="sticky top-14 z-10 mb-3 border-b border-border bg-bg/90 py-2 text-sm font-semibold text-text-dim backdrop-blur">
                {label}
              </h2>
              <div className="grid grid-cols-3 gap-3 md:grid-cols-4 lg:grid-cols-6">
                {items.map((p) => (
                  <div key={p.id} className="group relative aspect-square overflow-hidden rounded-md border border-border">
                    <img
                      src={p.thumbnailUrl}
                      alt={`照片 ${photoCaptureDayLabel(p.captureAt)}`}
                      loading="lazy"
                      className="size-full object-cover"
                    />
                    <button
                      type="button"
                      aria-label="删除这张照片"
                      className="absolute right-1 top-1 hidden size-11 items-center justify-center rounded-md bg-black/60 text-xs text-white hover:bg-danger focus-ring group-focus-within:flex group-hover:flex"
                      onClick={() => {
                        setDeleteError(null);
                        setPendingDelete(p);
                      }}
                    >
                      删除
                    </button>
                  </div>
                ))}
              </div>
            </section>
          ))}

          {nextCursor ? (
            <div className="flex flex-col items-center gap-2">
              {loadMoreError ? (
                <p role="status" className="text-sm text-danger">{loadMoreError}</p>
              ) : null}
              <button
                type="button"
                disabled={loadingMore}
                aria-busy={loadingMore}
                className="mx-auto inline-flex h-11 items-center rounded-md border border-border px-4 text-sm hover:border-accent/60 focus-ring disabled:cursor-not-allowed disabled:opacity-60"
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
        title="删除照片"
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
