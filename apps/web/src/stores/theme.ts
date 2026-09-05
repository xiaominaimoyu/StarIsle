import { atom } from "nanostores";

/**
 * 主题偏好:data-theme="dark|light" 应用在 <html>。
 * 防闪烁脚本(<head> 内联)只恢复主题偏好;本 store 负责运行时切换与持久化。
 */

export type Theme = "dark" | "light";

const THEME_KEY = "star-isle:theme";
const DEFAULT_THEME: Theme = "dark";

function detectInitialTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "dark" || saved === "light") return saved;
  } catch {
    // 隐私模式等场景忽略
  }
  return DEFAULT_THEME;
}

export const themeStore = atom<Theme>(detectInitialTheme());

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

export function toggleTheme(): void {
  const next: Theme = themeStore.get() === "dark" ? "light" : "dark";
  themeStore.set(next);
  applyTheme(next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    // 忽略持久化失败
  }
}

export function initTheme(): void {
  applyTheme(themeStore.get());
}