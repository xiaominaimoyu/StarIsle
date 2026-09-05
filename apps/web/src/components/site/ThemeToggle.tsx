import { useEffect, useState } from "react";
import { themeStore, toggleTheme, initTheme } from "@/stores/theme";

export default function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    initTheme();
    setTheme(themeStore.get());
    setMounted(true);
    const unsubscribe = themeStore.subscribe((t) => setTheme(t));
    return unsubscribe;
  }, []);

  const isLight = mounted && theme === "light";

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={isLight ? "切换到暗色主题" : "切换到亮色主题"}
      title={isLight ? "切换到暗色主题" : "切换到亮色主题"}
      className="inline-flex size-10 items-center justify-center rounded-md text-text-dim hover:bg-surface hover:text-text focus-ring"
    >
      {isLight ? (
        <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4" />
        </svg>
      ) : (
        <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
        </svg>
      )}
      <span className="sr-only">{isLight ? "切换到暗色主题" : "切换到亮色主题"}</span>
    </button>
  );
}