import { PRIVATE_NAV, safeNext } from "./private-state.mjs";

export async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function csrfToken() {
  const response = await fetch("/api/v1/auth/csrf", { credentials: "same-origin" });
  const body = await readJson(response);
  if (!response.ok || !body || typeof body.csrfToken !== "string") {
    throw new Error("csrf");
  }
  return body.csrfToken;
}

export async function ensureSession() {
  let me = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
  if (me.status === 401) {
    try {
      const token = await csrfToken();
      const refreshed = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": token },
      });
      if (!refreshed.ok) throw new Error("refresh");
    } catch {
      window.location.assign(`/private/login?next=${encodeURIComponent(safeNext(window.location.pathname))}`);
      return false;
    }
    me = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
  }
  if (!me.ok) {
    window.location.assign(`/private/login?next=${encodeURIComponent(safeNext(window.location.pathname))}`);
    return false;
  }
  return true;
}

export function renderNav(currentId) {
  const nav = document.querySelector("[data-nav]");
  if (!nav) return;
  nav.replaceChildren();
  for (const item of PRIVATE_NAV) {
    if (item.enabled && item.href) {
      const link = document.createElement("a");
      link.className = "nav-link";
      link.href = item.href;
      link.textContent = item.label;
      if (item.id === currentId) link.setAttribute("aria-current", "page");
      nav.append(link);
      continue;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "nav-link is-disabled";
    button.textContent = item.label;
    button.setAttribute("aria-disabled", "true");
    button.setAttribute("aria-label", `${item.label}，稍后开放`);
    const note = document.createElement("span");
    note.className = "nav-note";
    note.textContent = "稍后";
    button.append(note);
    button.addEventListener("click", (event) => event.preventDefault());
    nav.append(button);
  }
}

export function bindLogout() {
  const button = document.querySelector("[data-logout]");
  if (!button) return;
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const token = await csrfToken();
      await fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": token },
      });
    } finally {
      window.location.assign("/private/login");
    }
  });
}
