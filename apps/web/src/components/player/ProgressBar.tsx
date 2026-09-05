import { formatDuration } from "@/lib/format";

interface ProgressBarProps {
  position: number;
  duration: number;
  onSeek: (position: number) => void;
  disabled?: boolean;
}

export default function ProgressBar({ position, duration, onSeek, disabled }: ProgressBarProps) {
  return (
    <div className="flex items-center gap-2" aria-label="播放进度">
      <span className="w-10 text-right font-mono text-xs text-text-dim">
        {formatDuration(position)}
      </span>
      <input
        type="range"
        min={0}
        max={Math.max(0.01, duration)}
        step={1}
        value={Math.min(position, Math.max(0, duration))}
        disabled={disabled}
        aria-label="播放进度"
        aria-valuetext={`${formatDuration(position)} / ${formatDuration(duration)}`}
        onChange={(e) => onSeek(Number(e.target.value))}
        className="flex-1 accent-[color:var(--accent)]"
      />
      <span className="w-10 font-mono text-xs text-text-dim">{formatDuration(duration)}</span>
    </div>
  );
}
