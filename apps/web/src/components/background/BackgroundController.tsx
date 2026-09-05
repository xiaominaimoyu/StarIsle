import { useEffect, useRef, useState } from "react";
import {
  backgroundSettings,
  resetBackground,
  setBackgroundUrl,
  setBlur,
  setScrimOpacity,
} from "@/stores/background";
import {
  clearBackgroundBlob,
  saveBackgroundBlob,
} from "@/lib/db";
import { compressImage, MAX_ORIGINAL_BYTES } from "@/lib/image";
import Modal from "@/components/ui/Modal";

const PRESETS = [
  "/presets/backgrounds/starry-night.svg",
  "/presets/backgrounds/island-dawn.svg",
  "/presets/backgrounds/calm-paper.svg",
];

export default function BackgroundController() {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState(backgroundSettings.get());
  const [message, setMessage] = useState<{ tone: "error" | "info"; text: string } | null>(null);
  const [compressing, setCompressing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onOpen = () => setOpen(true);
    window.addEventListener("star-isle:open-background", onOpen);
    const unsubscribe = backgroundSettings.subscribe(setSettings);
    return () => {
      window.removeEventListener("star-isle:open-background", onOpen);
      unsubscribe();
    };
  }, []);

  const pickPreset = (url: string) => {
    setBackgroundUrl(url, false);
    setMessage(null);
  };

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    setCompressing(true);
    setMessage(null);
    const result = await compressImage(file);
    setCompressing(false);
    if (!result.ok) {
      setMessage({ tone: "error", text: result.reason });
      return;
    }
    const saved = await saveBackgroundBlob(result.blob);
    if (!saved.ok) {
      // IndexedDB 不可用:回退预设背景并明确说明无法本地持久化
      setBackgroundUrl(PRESETS[0]!, false);
      setMessage({
        tone: "info",
        text: "当前浏览器不支持本地保存背景,本次访问将使用预设背景。",
      });
      return;
    }
    const url = URL.createObjectURL(result.blob);
    setBackgroundUrl(url, true);
    setMessage(null);
  };

  return (
    <>
      <button
        type="button"
        aria-label="背景设置"
        title="背景设置"
        className="inline-flex size-10 items-center justify-center rounded-md text-text-dim hover:bg-surface hover:text-text focus-ring"
        onClick={() => setOpen(true)}
      >
        <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <circle cx="8.5" cy="8.5" r="1.5" />
          <path d="m21 15-5-5L5 21" />
        </svg>
        <span className="sr-only">背景设置</span>
      </button>

      <Modal
        open={open}
        title="自定义背景"
        onClose={() => setOpen(false)}
        cancelLabel="完成"
      >
        <div className="flex flex-col gap-4">
          {message ? (
            <div
              role="alert"
              className={`rounded-md border px-3 py-2 text-sm ${
                message.tone === "error"
                  ? "border-danger/40 text-danger"
                  : "border-accent/40 text-accent"
              }`}
            >
              {message.text}
            </div>
          ) : null}

          <div>
            <p className="mb-2 text-sm font-medium text-text">预设背景</p>
            <div className="grid grid-cols-3 gap-3">
              {PRESETS.map((url) => (
                <button
                  key={url}
                  type="button"
                  aria-label={`使用预设背景 ${url.split("/").pop()}`}
                  aria-pressed={settings.url === url && !settings.custom}
                  className={`h-16 overflow-hidden rounded-md border-2 focus-ring ${
                    settings.url === url && !settings.custom
                      ? "border-accent"
                      : "border-border hover:border-accent/50"
                  }`}
                  style={{ backgroundImage: `url(${url})`, backgroundSize: "cover" }}
                  onClick={() => pickPreset(url)}
                />
              ))}
            </div>
          </div>

          <div>
            <p className="mb-2 text-sm font-medium text-text">本地上传</p>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => handleFile(e.target.files?.[0])}
            />
            <button
              type="button"
              disabled={compressing}
              className="inline-flex h-10 items-center rounded-md border border-border px-4 text-sm hover:border-accent/60 focus-ring disabled:opacity-60"
              onClick={() => fileInputRef.current?.click()}
            >
              {compressing ? "处理中…" : "选择图片"}
            </button>
            <p className="mt-2 text-xs text-text-dim">
              仅保存在当前浏览器与设备;原图超过 {MAX_ORIGINAL_BYTES / 1024 / 1024} MB 会被拒绝,压缩后 Blob 目标不超过 1.5 MB。
            </p>
          </div>

          <label className="flex flex-col gap-1">
            <span className="text-sm font-medium text-text">
              scrim 遮罩透明度 <span className="text-text-dim">{Math.round(settings.scrimOpacity * 100)}%</span>
            </span>
            <input
              type="range"
              min={0}
              max={0.9}
              step={0.05}
              value={settings.scrimOpacity}
              onChange={(e) => setScrimOpacity(Number(e.target.value))}
              className="accent-[color:var(--accent)]"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-sm font-medium text-text">
              模糊 <span className="text-text-dim">{settings.blur}px</span>
            </span>
            <input
              type="range"
              min={0}
              max={20}
              step={1}
              value={settings.blur}
              onChange={(e) => setBlur(Number(e.target.value))}
              className="accent-[color:var(--accent)]"
            />
          </label>

          <button
            type="button"
            className="inline-flex h-10 items-center justify-center rounded-md border border-border text-sm hover:border-danger/60 hover:text-danger focus-ring"
            onClick={() => {
              resetBackground();
              clearBackgroundBlob();
              setMessage(null);
            }}
          >
            恢复默认
          </button>
        </div>
      </Modal>
    </>
  );
}
