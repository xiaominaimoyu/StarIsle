import { useEffect, useRef, type ReactNode } from "react";

interface ModalProps {
  open: boolean;
  title: string;
  variant?: "default" | "destructive";
  confirmLabel?: string;
  cancelLabel?: string;
  onClose: () => void;
  onConfirm?: () => void;
  confirmLoading?: boolean;
  children?: ReactNode;
}

export default function Modal({
  open,
  title,
  variant = "default",
  confirmLabel = "确认",
  cancelLabel = "取消",
  onClose,
  onConfirm,
  confirmLoading = false,
  children,
}: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previousFocus.current = document.activeElement as HTMLElement;
    dialogRef.current?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        // 焦点圈禁在对话框内
        const focusables = dialogRef.current?.querySelectorAll<HTMLElement>(
          'button, a[href], input, [tabindex]:not([tabindex="-1"])',
        );
        if (!focusables || focusables.length === 0) return;
        const first = focusables[0]!;
        const last = focusables[focusables.length - 1]!;
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previousFocus.current?.focus(); // 关闭后焦点还原
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="absolute inset-0 bg-black/60" onClick={onClose} aria-hidden="true" />
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="modal-enter card relative z-10 w-full max-w-2xl rounded-sm border-t-2 border-t-accent-warm p-6 shadow-2 focus-ring"
      >
        <h2 className="mb-2 text-xl font-semibold text-text">{title}</h2>
        {children ? <div className="mb-4 text-base text-text-dim">{children}</div> : null}
        <div className="flex justify-end gap-3">
          <button
            type="button"
            className="inline-flex h-10 items-center rounded-sm border border-border bg-surface/70 px-4 hover:border-accent-warm/70 focus-ring"
            onClick={onClose}
          >
            {cancelLabel}
          </button>
          {onConfirm ? (
            <button
              type="button"
              disabled={confirmLoading}
              className={`inline-flex h-10 items-center rounded-sm border px-4 font-medium text-ink focus-ring disabled:opacity-60 ${
                variant === "destructive"
                  ? "border-danger bg-danger hover:brightness-110"
                  : "border-accent bg-accent hover:brightness-110"
              }`}
              onClick={onConfirm}
            >
              {confirmLoading ? "处理中…" : confirmLabel}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
