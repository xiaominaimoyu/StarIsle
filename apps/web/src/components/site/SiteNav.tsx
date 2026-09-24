import { useEffect, useRef, useState } from "react";
import SealMonogramSvg from "@/components/narrative/SealMonogramSvg";

interface NavLink {
  href: string;
  label: string;
}

const LINKS: NavLink[] = [
  { href: "/", label: "首页" },
  { href: "/projects", label: "项目" },
  { href: "/resume", label: "简历" },
];

interface SiteNavProps {
  showAuth?: boolean;
}

export default function SiteNav({ showAuth = true }: SiteNavProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [pathname, setPathname] = useState("/");
  const drawerRef = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    setPathname(window.location.pathname);
  }, []);

  useEffect(() => {
    if (!drawerOpen) return;
    previousFocus.current = document.activeElement as HTMLElement;
    drawerRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previousFocus.current?.focus();
    };
  }, [drawerOpen]);

  const isCurrent = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  const links = (
    <>
      {LINKS.map((link) => (
        <a
          key={link.href}
          href={link.href}
          aria-current={isCurrent(link.href) ? "page" : undefined}
          className="rounded-sm border-b border-transparent px-3 py-2 text-sm text-text-dim hover:border-accent-warm hover:text-text aria-[current=page]:border-accent aria-[current=page]:text-text focus-ring"
        >
          {link.label}
        </a>
      ))}
      {showAuth ? (
        <a
          href="/console"
          className="rounded-sm border-b border-transparent px-3 py-2 text-sm text-text-dim hover:border-accent-warm hover:text-text focus-ring"
        >
          控制台
        </a>
      ) : null}
    </>
  );

  return (
    <>
      {/* 桌面导航 */}
      <nav aria-label="主导航" className="hidden items-center gap-1 md:flex">
        {links}
      </nav>

      {/* 移动端:汉堡 + 抽屉 */}
      <button
        type="button"
        className="inline-flex size-10 items-center justify-center rounded-sm border border-transparent text-text-dim hover:border-border hover:bg-surface hover:text-text focus-ring md:hidden"
        aria-expanded={drawerOpen}
        aria-controls="mobile-drawer"
        aria-label="打开导航菜单"
        onClick={() => setDrawerOpen(true)}
      >
        <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <path d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      {drawerOpen ? (
        <div className="fixed inset-0 z-60 md:hidden" role="dialog" aria-modal="true" aria-label="导航菜单">
          <div className="absolute inset-0 bg-black/60" onClick={() => setDrawerOpen(false)} aria-hidden="true" />
          <div
            ref={drawerRef}
            tabIndex={-1}
            className="modal-enter absolute right-0 top-0 flex h-full w-64 flex-col gap-1 border-l-2 border-l-accent-warm bg-surface p-4 shadow-2"
          >
            <div className="mb-3 flex items-center justify-between">
              <span className="flex items-center gap-2 font-display text-lg font-semibold text-text">
                <SealMonogramSvg className="brand-seal" variant="compact" /> 星屿
              </span>
              <button
                type="button"
                aria-label="关闭导航菜单"
                className="inline-flex size-10 items-center justify-center rounded-sm border border-transparent text-text-dim hover:border-border hover:bg-bg focus-ring"
                onClick={() => setDrawerOpen(false)}
              >
                ✕
              </button>
            </div>
            {links}
          </div>
        </div>
      ) : null}
    </>
  );
}
