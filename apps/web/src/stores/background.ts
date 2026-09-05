import { atom } from "nanostores";

/**
 * 背景状态:预设背景或 IndexedDB Blob(URL),与主题正交。
 * scrim 透明度与模糊度仅保存在浏览器偏好中。
 */

export interface BackgroundSettings {
  /** null = 默认(无自定义背景) */
  url: string | null;
  /** 是否来自用户上传(IndexedDB Blob) */
  custom: boolean;
  scrimOpacity: number; // 0..1
  blur: number; // 0..20 px
}

const KEY = "star-isle:background-settings";

function loadSettings(): BackgroundSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<BackgroundSettings>;
      return {
        url: parsed.url ?? null,
        custom: parsed.custom ?? false,
        scrimOpacity: parsed.scrimOpacity ?? 0.45,
        blur: parsed.blur ?? 0,
      };
    }
  } catch {
    // 忽略
  }
  return { url: null, custom: false, scrimOpacity: 0.45, blur: 0 };
}

export const backgroundSettings = atom<BackgroundSettings>(loadSettings());

function persist(next: BackgroundSettings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // 忽略持久化失败
  }
}

export function setBackgroundUrl(url: string | null, custom: boolean): void {
  const next = { ...backgroundSettings.get(), url, custom };
  backgroundSettings.set(next);
  persist(next);
}

export function setScrimOpacity(value: number): void {
  const next = { ...backgroundSettings.get(), scrimOpacity: value };
  backgroundSettings.set(next);
  persist(next);
}

export function setBlur(value: number): void {
  const next = { ...backgroundSettings.get(), blur: value };
  backgroundSettings.set(next);
  persist(next);
}

export function resetBackground(): void {
  const next = { ...backgroundSettings.get(), url: null, custom: false };
  backgroundSettings.set(next);
  persist(next);
}