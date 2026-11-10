import { deletionRequest } from "./private-state.mjs";
import { bindLogout, csrfToken, ensureSession, readJson, renderNav } from "./desk.js";

const list = document.querySelector("[data-list]");
const empty = document.querySelector("[data-empty]");
const status = document.querySelector("[data-status]");
const dialog = document.querySelector("#letter-dialog");
const confirmDialog = document.querySelector("#confirm-delete");
let view = "inbox";
let letters = [];
let pendingId = "";

const PULL_TEXT = {
  VISITOR_PULL_OK: "收取完成。",
  VISITOR_PULL_OFFLINE: "云端暂时连不上，本地来信还在。",
  VISITOR_PULL_SKIPPED: "还没有配置收取密钥。",
  VISITOR_PULL_UNAUTHORIZED: "收取密钥不对。",
};

function note(message, ok) {
  status.hidden = false;
  status.textContent = message;
  status.className = ok ? "ok-note" : "alert";
}

function render() {
  list.replaceChildren();
  empty.hidden = letters.length !== 0;
  for (const letter of letters) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "letter-row";
    const title = document.createElement("strong");
    title.textContent = letter.name || "未留名";
    const when = document.createElement("span");
    when.textContent = letter.read ? "已读" : "未读";
    button.append(title, when);
    button.addEventListener("click", () => openLetter(letter));
    list.append(button);
  }
}

async function load() {
  const response = await fetch(`/api/v1/visitor-letters?view=${view}`, { credentials: "same-origin" });
  if (response.status === 401) {
    window.location.assign("/private/login?next=/private/messages");
    return;
  }
  if (!response.ok) {
    note("来信没有载入。", false);
    return;
  }
  const body = await response.json();
  letters = Array.isArray(body.items) ? body.items : [];
  render();
}

async function mutate(path, method) {
  const token = await csrfToken();
  return fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "X-CSRF-Token": token },
  });
}

async function openLetter(letter) {
  document.querySelector("#letter-name").textContent = letter.name || "未留名";
  document.querySelector("#letter-meta").textContent = letter.contact || "";
  document.querySelector("#letter-body").textContent = letter.body || "";
  pendingId = letter.id;
  dialog.showModal();
  if (!letter.read) {
    await mutate(`/api/v1/visitor-letters/${encodeURIComponent(letter.id)}/read`, "POST");
    letter.read = true;
    render();
  }
}

if (await ensureSession()) {
  renderNav("messages");
  bindLogout();
  await load();
  const wantedLetter = new URLSearchParams(window.location.search).get("letter");
  if (wantedLetter) {
    const letter = letters.find((item) => item.id === wantedLetter);
    if (letter) await openLetter(letter);
  }
  document.querySelector("[data-pull]").addEventListener("click", async () => {
    try {
      const token = await csrfToken();
      const response = await fetch("/api/v1/visitor-letters/pull", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": token },
      });
      const body = await readJson(response);
      const code = body && body.code;
      note(PULL_TEXT[code] || "收取没有完成。", code === "VISITOR_PULL_OK" || code === "VISITOR_PULL_SKIPPED");
      if (code === "VISITOR_PULL_OK" && body.imported) note(`收取完成，新来信 ${body.imported} 封。`, true);
      await load();
    } catch {
      note("云端暂时连不上，本地来信还在。", false);
    }
  });
  for (const button of document.querySelectorAll("[data-view]")) {
    button.addEventListener("click", async () => {
      view = button.getAttribute("data-view");
      await load();
    });
  }
  document.querySelector("#letter-close").addEventListener("click", () => dialog.close());
  document.querySelector("#letter-archive").addEventListener("click", async () => {
    if (!pendingId) return;
    await mutate(`/api/v1/visitor-letters/${encodeURIComponent(pendingId)}/archive`, "POST");
    dialog.close();
    await load();
  });
  document.querySelector("#letter-delete").addEventListener("click", () => confirmDialog.showModal());
  document.querySelector("#confirm-cancel").addEventListener("click", () => confirmDialog.close());
  document.querySelector("#confirm-ok").addEventListener("click", async () => {
    const decision = deletionRequest(pendingId, true);
    confirmDialog.close();
    dialog.close();
    if (!decision.ok) return;
    await mutate(`/api/v1/visitor-letters/${encodeURIComponent(decision.photoId)}`, "DELETE");
    pendingId = "";
    await load();
  });
}
