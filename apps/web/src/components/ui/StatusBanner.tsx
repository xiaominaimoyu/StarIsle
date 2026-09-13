import { useEffect, useState, type ReactNode } from "react";

export type BannerTone = "info" | "success" | "warning" | "error";

interface StatusBannerProps {
  tone: BannerTone;
  children: ReactNode;
  action?: ReactNode;
  closable?: boolean;
  onClose?: () => void;
  /** 动态出现走 aria-live="polite" */
  live?: boolean;
}

const toneClass: Record<BannerTone, string> = {
  info: "border-accent/40 text-accent",
  success: "border-success/40 text-success",
  warning: "border-accent-warm/40 text-accent-warm",
  error: "border-danger/40 text-danger",
};

const toneIcon: Record<BannerTone, string> = {
  info: "ℹ",
  success: "✓",
  warning: "⚠",
  error: "✕",
};

export default function StatusBanner({
  tone,
  children,
  action,
  closable = false,
  onClose,
  live = false,
}: StatusBannerProps) {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    setVisible(true);
  }, [children]);

  if (!visible) return null;

  return (
    <div
      role={live ? "status" : undefined}
      aria-live={live ? "polite" : undefined}
      className={`flex items-center gap-2 rounded-sm border border-l-2 bg-surface px-3 py-2 text-sm ${toneClass[tone]}`}
    >
      <span aria-hidden="true" className="shrink-0">{toneIcon[tone]}</span>
      <span className="flex-1 text-text">{children}</span>
      {action ? <span className="shrink-0">{action}</span> : null}
      {closable ? (
        <button
          type="button"
          aria-label="关闭提示"
          className="shrink-0 text-text-dim hover:text-text focus-ring"
          onClick={() => {
            setVisible(false);
            onClose?.();
          }}
        >
          ×
        </button>
      ) : null}
    </div>
  );
}
