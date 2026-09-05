import type { ReactNode } from "react";

interface TagProps {
  children: ReactNode;
  tone?: "accent" | "warm" | "neutral";
  onRemove?: () => void;
}

export default function Tag({ children, tone = "accent", onRemove }: TagProps) {
  const tones = {
    accent: "border-accent/50 text-accent",
    warm: "border-accent-warm/50 text-accent-warm",
    neutral: "border-border text-text-dim",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs ${tones[tone]}`}
    >
      {children}
      {onRemove ? (
        <button
          type="button"
          aria-label="移除标签"
          className="ml-1 inline-flex size-4 items-center justify-center rounded-full text-current hover:bg-surface focus-ring"
          onClick={onRemove}
        >
          ×
        </button>
      ) : null}
    </span>
  );
}