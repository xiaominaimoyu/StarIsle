import { atom } from "nanostores";

/**
 * 播放器全局状态(迷你条与歌单面板共享):
 * 一期核心验收为进度续播(位置与音量持久化)。
 */

export interface Track {
  id: string;
  title: string;
  artist: string;
  src: string;
  cover?: string;
  duration?: number;
}

export interface PlayerState {
  queue: Track[];
  currentIndex: number;
  isPlaying: boolean;
  position: number;
  volume: number;
  mediaError: boolean;
}

const VOLUME_KEY = "star-isle:player-volume";
const POSITION_KEY = "star-isle:player-position";

export const playerState = atom<PlayerState>({
  queue: [],
  currentIndex: -1,
  isPlaying: false,
  position: 0,
  volume: loadVolume(),
  mediaError: false,
});

function loadVolume(): number {
  try {
    const v = Number(localStorage.getItem(VOLUME_KEY));
    if (Number.isFinite(v) && v >= 0 && v <= 1) return v;
  } catch {
    // 忽略
  }
  return 0.8;
}

export function setQueue(queue: Track[], startIndex = 0): void {
  playerState.set({
    ...playerState.get(),
    queue,
    currentIndex: queue.length ? startIndex : -1,
    isPlaying: false,
    position: 0,
    mediaError: false,
  });
}

export function playTrack(index: number): void {
  const s = playerState.get();
  if (index < 0 || index >= s.queue.length) return;
  playerState.set({ ...s, currentIndex: index, isPlaying: true, mediaError: false });
}

export function togglePlay(): void {
  const s = playerState.get();
  if (s.currentIndex < 0 && s.queue.length > 0) {
    playerState.set({ ...s, currentIndex: 0, isPlaying: true });
    return;
  }
  playerState.set({ ...s, isPlaying: !s.isPlaying });
}

export function nextTrack(): void {
  const s = playerState.get();
  if (!s.queue.length) return;
  const idx = (s.currentIndex + 1) % s.queue.length;
  playTrack(idx);
}

export function prevTrack(): void {
  const s = playerState.get();
  if (!s.queue.length) return;
  const idx = (s.currentIndex - 1 + s.queue.length) % s.queue.length;
  playTrack(idx);
}

export function setPosition(position: number): void {
  playerState.set({ ...playerState.get(), position });
  try {
    localStorage.setItem(POSITION_KEY, String(position));
  } catch {
    // 忽略
  }
}

export function setVolume(volume: number): void {
  playerState.set({ ...playerState.get(), volume });
  try {
    localStorage.setItem(VOLUME_KEY, String(volume));
  } catch {
    // 忽略
  }
}

export function setMediaError(mediaError: boolean): void {
  playerState.set({ ...playerState.get(), mediaError });
}

/** 页面恢复时按轨迹续播 */
export function restorePosition(trackId: string): number {
  try {
    const raw = localStorage.getItem(`${POSITION_KEY}:${trackId}`);
    const v = Number(raw);
    if (Number.isFinite(v) && v > 0) return v;
  } catch {
    // 忽略
  }
  return 0;
}