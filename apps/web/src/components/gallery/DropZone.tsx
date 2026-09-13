import { useCallback, useRef, useState, type DragEvent } from "react";

export interface UploadItem {
  id: string;
  file: File;
  status: "queued" | "uploading" | "done" | "error";
  progress: number; // 0..100
  error?: string;
}

interface DropZoneProps {
  accept?: string;
  maxBytes: number;
  maxConcurrency?: number;
  onUpload: (file: File, item: UploadItem, update: (patch: Partial<UploadItem>) => void) => Promise<void>;
  label?: string;
}

/**
 * 上传区:多选 + 拖放,单项进度条,并发 ≤ 3。
 * 类型/大小超限即时拒绝并给出原因;服务端校验仍是决定性边界。
 */
export default function DropZone({
  accept,
  maxBytes,
  maxConcurrency = 3,
  onUpload,
  label = "拖放文件到此处,或点击选择",
}: DropZoneProps) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const itemsRef = useRef<UploadItem[]>([]);
  const pendingIds = useRef<string[]>([]);
  const activeIds = useRef(new Set<string>());
  const activeCount = useRef(0);
  const nextItemId = useRef(0);
  const pumpRef = useRef<() => void>(() => {});

  const updateItem = useCallback((id: string, patch: Partial<UploadItem>) => {
    const next = itemsRef.current.map((item) => (
      item.id === id ? { ...item, ...patch, id: item.id } : item
    ));
    itemsRef.current = next;
    setItems(next);
  }, []);

  const runUpload = useCallback(async (item: UploadItem) => {
    try {
      await onUpload(item.file, item, (patch) => updateItem(item.id, patch));
      updateItem(item.id, { status: "done", progress: 100 });
    } catch (err) {
      updateItem(item.id, {
        status: "error",
        error: err instanceof Error ? err.message : "上传失败",
      });
    } finally {
      // 每个 item 在 pump 中只会认领一次；这里与认领一一对应，计数不会负数。
      if (activeIds.current.delete(item.id)) {
        activeCount.current -= 1;
      }
      pumpRef.current();
    }
  }, [onUpload, updateItem]);

  // pendingIds 是唯一待处理队列；同步认领后才启动异步上传，最大并发始终限制为 3。
  const pump = useCallback(() => {
    const concurrency = Math.min(Math.max(maxConcurrency, 1), 3);
    while (activeCount.current < concurrency) {
      const id = pendingIds.current.shift();
      if (!id) return;

      const item = itemsRef.current.find((candidate) => candidate.id === id);
      if (!item || item.status !== "queued" || activeIds.current.has(id)) continue;

      activeIds.current.add(id);
      activeCount.current += 1;
      updateItem(id, { status: "uploading" });
      void runUpload(item);
    }
  }, [maxConcurrency, runUpload, updateItem]);

  pumpRef.current = pump;

  const addFiles = (files: FileList | File[]) => {
    const list = Array.from(files);
    const next: UploadItem[] = list.map((file) => {
      const id = `upload-${nextItemId.current++}`;
      if (file.size > maxBytes) {
        return {
          id,
          file,
          status: "error" as const,
          progress: 0,
          error: `文件超过大小上限(${Math.round(maxBytes / 1024 / 1024)} MB),请压缩后重试。`,
        };
      }
      if (accept && !accept.split(",").some((a) => file.type === a.trim())) {
        return {
          id,
          file,
          status: "error" as const,
          progress: 0,
          error: "暂不支持该文件类型。",
        };
      }
      return { id, file, status: "queued" as const, progress: 0 };
    });

    const merged = [...itemsRef.current, ...next];
    itemsRef.current = merged;
    setItems(merged);
    pendingIds.current.push(...next.filter((item) => item.status === "queued").map((item) => item.id));
    pumpRef.current();
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  };

  return (
    <div className="flex flex-col gap-3">
      <div
        role="button"
        tabIndex={0}
        aria-label={label}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-sm border-2 border-dashed bg-surface/40 p-6 text-sm transition-colors focus-ring ${
          dragging ? "border-accent-warm bg-accent-warm/10" : "border-border hover:border-accent-warm/60"
        }`}
      >
        <span aria-hidden="true" className="text-2xl text-accent">⬆</span>
        <span className="text-text-dim">{label}</span>
      </div>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={accept}
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) addFiles(e.target.files);
          e.target.value = "";
        }}
      />

      {items.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li key={item.id} className="card flex items-center gap-3 rounded-sm border-l-2 border-l-accent-warm/60 p-3">
              <span className="min-w-0 flex-1 truncate text-sm text-text">{item.file.name}</span>
              {item.status === "error" ? (
                <span role="alert" className="shrink-0 text-xs text-danger">{item.error}</span>
              ) : item.status === "done" ? (
                <span className="shrink-0 text-xs text-success">✓ 完成</span>
              ) : (
                <div className="flex shrink-0 items-center gap-2">
                  <div className="h-1.5 w-24 overflow-hidden rounded-none bg-border" role="progressbar" aria-valuenow={item.progress} aria-valuemin={0} aria-valuemax={100} aria-label={`${item.file.name} 上传进度`}>
                    <div className="h-full bg-accent transition-[transform] duration-200" style={{ transform: `scaleX(${item.progress / 100})`, transformOrigin: "left" }} />
                  </div>
                  <span className="w-8 text-right text-xs text-text-dim">{item.progress}%</span>
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
