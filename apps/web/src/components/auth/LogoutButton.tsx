import { useState } from "react";
import { logout, fetchCsrf } from "@/lib/api/modules";
import { getCsrf, setAnonymous } from "@/stores/session";

export default function LogoutButton() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLogout = async () => {
    setBusy(true);
    setError(null);
    try {
      let token = getCsrf();
      if (!token) {
        const csrf = await fetchCsrf();
        token = csrf.csrfToken;
      }
      await logout(token);
      setAnonymous();
      window.location.href = "/";
    } catch {
      setError("退出失败,请重试。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        disabled={busy}
        className="inline-flex h-10 items-center rounded-md border border-border px-3 text-sm text-text-dim hover:border-danger/60 hover:text-danger focus-ring disabled:opacity-60"
        onClick={handleLogout}
      >
        {busy ? "退出中…" : "退出"}
      </button>
      {error ? (
        <span role="alert" className="text-sm text-danger">{error}</span>
      ) : null}
    </>
  );
}