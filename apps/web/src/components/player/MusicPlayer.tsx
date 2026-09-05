import { useEffect, useRef, useState } from "react";
import {
  nextTrack,
  playTrack,
  playerState,
  prevTrack,
  setQueue,
  setPosition,
  setVolume,
  togglePlay,
  type Track,
} from "@/stores/player";
import ProgressBar from "./ProgressBar";

/**
 * 底部迷你条(z50)+ 歌单面板(z60),共享 nanostores 状态。
 * 一期核心:播放/暂停、切歌、进度续播、音量;键盘 ←/→ 步进 5 秒、空格播放暂停(输入框聚焦时不劫持)。
 */
export default function MusicPlayer({ initialQueue = [] }: { initialQueue?: Track[] }) {
  const [state, setState] = useState(playerState.get());
  const [panelOpen, setPanelOpen] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const unsubscribe = playerState.subscribe(setState);
    const onOpen = () => setPanelOpen(true);
    window.addEventListener("star-isle:open-playlist", onOpen);
    return () => {
      unsubscribe();
      window.removeEventListener("star-isle:open-playlist", onOpen);
    };
  }, []);

  // 歌单由受跟踪的静态数据提供；空列表是明确的可用空态。
  useEffect(() => {
    setQueue(initialQueue);
  }, [initialQueue]);

  const track: Track | null = state.queue[state.currentIndex] ?? null;

  // 音频元素同步状态
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !track) return;
    if (audio.src !== track.src) {
      audio.src = track.src;
      // 进度续播:同曲目恢复上次位置
      try {
        const saved = Number(localStorage.getItem(`star-isle:player-position:${track.id}`));
        if (Number.isFinite(saved) && saved > 0 && saved < (track.duration ?? 9999)) {
          audio.currentTime = saved;
        }
      } catch {
        // 忽略
      }
    }
    if (state.isPlaying) {
      audio.play().catch(() => {
        // 自动播放被拦截:显示待点击的播放钮,不静音偷跑
      });
    } else {
      audio.pause();
    }
  }, [state.currentIndex, state.isPlaying, state.queue]);

  // 键盘快捷键(输入框聚焦时不劫持)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable) return;
      const audio = audioRef.current;
      if (!audio || !track) return;
      if (e.code === "Space") {
        e.preventDefault();
        togglePlay();
      } else if (e.code === "ArrowLeft") {
        e.preventDefault();
        audio.currentTime = Math.max(0, audio.currentTime - 5);
      } else if (e.code === "ArrowRight") {
        e.preventDefault();
        audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 5);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [track, state.isPlaying]);

  if (!track) {
    return (
      <div className="minibar-enter no-print fixed inset-x-0 bottom-0 z-50 border-t border-border bg-surface/95 px-4 py-3 shadow-2 backdrop-blur" style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}>
        <button
          type="button"
          className="inline-flex h-10 items-center gap-2 rounded-md border border-border px-3 text-sm text-text-dim hover:border-accent/60 focus-ring"
          onClick={() => setPanelOpen(true)}
        >
          <span aria-hidden="true">♪</span> 音乐播放器
        </button>
      </div>
    );
  }

  return (
    <>
      <div
        className="minibar-enter no-print fixed inset-x-0 bottom-0 z-50 border-t border-border bg-surface/95 px-4 py-3 shadow-2 backdrop-blur"
        style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
      >
        <audio
          ref={audioRef}
          onTimeUpdate={(e) => {
            const audio = e.currentTarget;
            setPosition(audio.currentTime);
            try {
              localStorage.setItem(`star-isle:player-position:${track.id}`, String(audio.currentTime));
            } catch {
              // 忽略
            }
          }}
          onEnded={nextTrack}
          onError={() => setPosition(0)}
          preload="metadata"
        />
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            {track.cover ? (
              <img src={track.cover} alt="" className="size-10 shrink-0 rounded-md object-cover" loading="lazy" />
            ) : (
              <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-accent/15 text-accent" aria-hidden="true">♪</div>
            )}
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-text">{track.title}</p>
              <p className="truncate text-xs text-text-dim">{track.artist}</p>
            </div>
          </div>

          <div className="flex items-center gap-1">
            <button type="button" aria-label="上一首" className="inline-flex size-10 items-center justify-center rounded-md text-text-dim hover:text-text focus-ring" onClick={prevTrack}>
              ⏮
            </button>
            <button
              type="button"
              aria-label={state.isPlaying ? "暂停" : "播放"}
              className="inline-flex size-11 items-center justify-center rounded-full bg-accent text-accent-foreground hover:brightness-110 focus-ring"
              onClick={togglePlay}
            >
              {state.isPlaying ? "⏸" : "▶"}
            </button>
            <button type="button" aria-label="下一首" className="inline-flex size-10 items-center justify-center rounded-md text-text-dim hover:text-text focus-ring" onClick={nextTrack}>
              ⏭
            </button>
          </div>

          <div className="hidden min-w-52 flex-1 md:block">
            <ProgressBar
              position={state.position}
              duration={track.duration ?? 0}
              disabled={!track.duration}
              onSeek={(pos) => {
                const audio = audioRef.current;
                if (audio) audio.currentTime = pos;
                setPosition(pos);
              }}
            />
          </div>

          <div className="flex items-center gap-1">
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={state.volume}
              aria-label="音量"
              onChange={(e) => {
                const v = Number(e.target.value);
                setVolume(v);
                if (audioRef.current) audioRef.current.volume = v;
              }}
              className="w-20 accent-[color:var(--accent)]"
            />
            <button
              type="button"
              aria-label="打开歌单"
              aria-expanded={panelOpen}
              className="inline-flex size-10 items-center justify-center rounded-md text-text-dim hover:text-text focus-ring"
              onClick={() => setPanelOpen((v) => !v)}
            >
              ≡
            </button>
          </div>
        </div>
        {state.mediaError ? (
          <p className="mt-1 text-center text-xs text-danger" role="alert">
            该曲目暂时无法播放,可手动切换到下一首。
          </p>
        ) : null}
      </div>
      {panelOpen ? (
        <PlaylistPanel open={panelOpen} onClose={() => setPanelOpen(false)} queue={state.queue} currentIndex={state.currentIndex} />
      ) : null}
    </>
  );
}

function PlaylistPanel({
  open,
  onClose,
  queue,
  currentIndex,
}: {
  open: boolean;
  onClose: () => void;
  queue: Track[];
  currentIndex: number;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-60" role="dialog" aria-modal="true" aria-label="歌单">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} aria-hidden="true" />
      <div className="modal-enter card absolute right-0 top-0 flex h-full w-80 flex-col p-4 shadow-2">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-semibold text-text">歌单</h3>
          <button type="button" aria-label="关闭歌单" className="inline-flex size-10 items-center justify-center rounded-md text-text-dim hover:text-text focus-ring" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {queue.length === 0 ? (
            <p className="py-8 text-center text-sm text-text-dim">
              暂无曲目。请将自有、授权或公版音频放入 <code className="font-mono">public/music/</code> 并更新播放列表。
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {queue.map((t, i) => (
                <li key={t.id}>
                  <button
                    type="button"
                    className={`w-full rounded-md px-3 py-2 text-left text-sm focus-ring ${
                      i === currentIndex ? "bg-accent/15 text-accent" : "text-text hover:bg-surface"
                    }`}
                    onClick={() => playTrack(i)}
                  >
                    <span className="block truncate font-medium">{t.title}</span>
                    <span className="block truncate text-xs text-text-dim">{t.artist}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
