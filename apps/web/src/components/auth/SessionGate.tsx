import { useEffect, useState, type ReactNode } from "react";
import { fetchMe, fetchCsrf, refresh } from "@/lib/api/modules";
import { ApiError } from "@/lib/api/client";
import { setAuthenticated, setAnonymous, setCsrf, getCsrf } from "@/stores/session";

type GateStatus = "checking" | "authed" | "anonymous" | "forbidden" | "error";

function isAuthenticationFailure(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401 && error.code === "AUTH_REQUIRED";
}

/**
 * SessionGate:校验中显示骨架屏(标题块 + 卡片块,不做整屏 spinner);
 * 401 → 跳登录(带 redirect);403 → 页内"无权访问"。
 * 真实鉴权以后端为准,路由守卫只改善体验。
 */
export default function SessionGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<GateStatus>("checking");
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setStatus("checking");
    (async () => {
      // 先确保 CSRF token 就绪,供后续写操作使用
      if (!getCsrf()) {
        try {
          const csrf = await fetchCsrf();
          if (!cancelled) setCsrf(csrf.csrfToken, new Date(csrf.expiresAt).getTime());
        } catch {
          // 匿名场景下 CSRF 失败不阻塞;写操作时会重试
        }
      }
      try {
        const user = await fetchMe();
        if (cancelled) return;
        setAuthenticated(user);
        setStatus("authed");
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 403) {
          setStatus("forbidden");
          return;
        }
        // 仅在进入受保护页面时处理 access 过期：登录表单的 401 绝不触发 refresh。
        if (err instanceof ApiError && err.status === 401) {
          try {
            let token = getCsrf();
            if (!token) {
              const csrf = await fetchCsrf();
              token = csrf.csrfToken;
              if (!cancelled) setCsrf(token, new Date(csrf.expiresAt).getTime());
            }
            const user = await refresh(token);
            if (cancelled) return;
            setAuthenticated(user);
            setStatus("authed");
            return;
          } catch (refreshError) {
            if (cancelled) return;
            // refresh 不重试、不递归；只有明确的鉴权失败才按匿名会话处理。
            if (isAuthenticationFailure(refreshError)) {
              setAnonymous();
              const redirect = encodeURIComponent(window.location.pathname + window.location.search);
              window.location.href = `/login?redirect=${redirect}`;
              return;
            }
            setStatus("error");
            return;
          }
        }
        setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [retryVersion]);

  if (status === "checking") {
    return (
      <div className="flex flex-col gap-4" aria-busy="true" aria-label="会话校验中">
        <div className="skeleton h-6 w-48 rounded-md" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="skeleton h-28 rounded-card" />
          <div className="skeleton h-28 rounded-card" />
          <div className="skeleton h-28 rounded-card" />
          <div className="skeleton h-28 rounded-card" />
        </div>
        <div className="skeleton h-64 rounded-card" />
      </div>
    );
  }

  if (status === "forbidden") {
    return (
      <div className="card p-8 text-center">
        <h2 className="mb-2 text-lg font-semibold text-text">无权访问</h2>
        <p className="text-sm text-text-dim">当前会话无权访问该内容,请重新登录。</p>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="card p-8 text-center">
        <h2 className="mb-2 text-lg font-semibold text-text">会话校验失败</h2>
        <p role="alert" className="mb-4 text-sm text-text-dim">服务暂时不可用,请检查连接后重试。</p>
        <button
          type="button"
          className="inline-flex h-11 items-center rounded-md border border-border px-4 text-sm text-text-dim hover:border-accent/60 hover:text-text focus-ring"
          onClick={() => setRetryVersion((current) => current + 1)}
        >
          重试
        </button>
      </div>
    );
  }

  if (status !== "authed") return null;
  return <>{children}</>;
}
