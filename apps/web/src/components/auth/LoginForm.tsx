import { useEffect, useRef, useState, type SubmitEvent } from "react";
import { login, fetchCsrf } from "@/lib/api/modules";
import { ApiError } from "@/lib/api/client";
import { getCsrf, setAuthenticated, setCsrf } from "@/stores/session";
import FloatInput from "./FloatInput";
import Button from "@/components/ui/Button";

const PASSWORD_ERROR_ID = "login-password-error";

interface LoginFailure {
  code: string;
  message: string;
  cooldownSeconds?: number;
}

export function resolveLoginRedirect(search: string, origin: string): string {
  try {
    const raw = new URLSearchParams(search).get("redirect") ?? "/console";
    if (!raw.startsWith("/") || raw.startsWith("//")) return "/console";
    const target = new URL(raw, origin);
    return target.origin === origin ? raw : "/console";
  } catch {
    return "/console";
  }
}

export function classifyLoginFailure(error: unknown): LoginFailure {
  if (!(error instanceof ApiError)) {
    return { code: "unknown", message: "网络请求失败,请检查连接后重试。" };
  }
  if (error.status === 401) {
    return { code: "401", message: "无法登录,请检查密码后重试。" };
  }
  if (error.status === 429) {
    return {
      code: "429",
      message: "尝试过于频繁,请稍候再试。",
      cooldownSeconds: 30,
    };
  }
  if (error.code === "CSRF_REQUIRED" || error.status === 0) {
    return { code: "csrf", message: "安全验证获取失败,请重试。" };
  }
  return { code: String(error.status), message: error.message };
}

export async function loginWithCsrf(password: string) {
  let token = getCsrf();
  if (!token) {
    const csrf = await fetchCsrf();
    setCsrf(csrf.csrfToken, new Date(csrf.expiresAt).getTime());
    token = csrf.csrfToken;
  }

  try {
    return await login({ password }, token);
  } catch (error) {
    if (error instanceof ApiError && error.code === "CSRF_REQUIRED") {
      setCsrf(null, null);
    }
    throw error;
  }
}

/**
 * 单密码登录(单用户契约 POST /auth/login 仅需 { password })。
 * 状态:401 通用失败文案;429 冷却倒计时;CSRF 失败可重试;成功跳回 redirect(仅站内相对路径)。
 */
export default function LoginForm() {
  const [ready, setReady] = useState(false);
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    setReady(true);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, []);

  const startCooldown = (seconds: number) => {
    setCooldown(seconds);
    timerRef.current = window.setInterval(() => {
      setCooldown((s) => {
        if (s <= 1 && timerRef.current) window.clearInterval(timerRef.current);
        return Math.max(0, s - 1);
      });
    }, 1000);
  };

  const handleSubmit = async (e: SubmitEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!ready || submitting || cooldown > 0) return;
    if (!password) {
      setError({ code: "empty", message: "请输入密码。" });
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const user = await loginWithCsrf(password);
      setAuthenticated(user);
      const redirect = resolveLoginRedirect(window.location.search, window.location.origin);
      window.location.href = redirect;
    } catch (err) {
      const failure = classifyLoginFailure(err);
      setError({ code: failure.code, message: failure.message });
      if (failure.cooldownSeconds) startCooldown(failure.cooldownSeconds);
    } finally {
      setSubmitting(false);
    }
  };

  const hasPasswordError = error?.code === "empty" || error?.code === "401";

  return (
    <form onSubmit={handleSubmit} noValidate aria-busy={!ready || submitting} className="flex flex-col gap-3">
      <FloatInput
        label="密码"
        type="password"
        password
        disabled={!ready || submitting}
        autoComplete="current-password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        required
        invalid={hasPasswordError}
        aria-describedby={error ? PASSWORD_ERROR_ID : undefined}
      />
      <Button type="submit" size="lg" loading={submitting} disabled={!ready || cooldown > 0} className="w-full">
        {!ready ? "准备登录…" : cooldown > 0 ? `请 ${cooldown}s 后重试` : "登录"}
      </Button>
      <div aria-live="polite">
        {error ? (
          <p id={PASSWORD_ERROR_ID} role="alert" className="text-sm text-danger">
            {error.message}
          </p>
        ) : null}
      </div>
    </form>
  );
}
