import { deletionRequest } from "./private-state.mjs";
import { bindLogout, csrfToken, ensureSession, renderNav } from "./desk.js";

const todoList = document.querySelector("[data-todos]");
const todoEmpty = document.querySelector("[data-todo-empty]");
const activityList = document.querySelector("[data-activity]");
const activityEmpty = document.querySelector("[data-activity-empty]");
const confirmDialog = document.querySelector("#confirm-delete");
let pendingDelete = "";

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = await csrfToken();
  if (options.body) headers["Content-Type"] = "application/json";
  return fetch(path, { credentials: "same-origin", ...options, headers });
}

function paint(summary) {
  const counts = summary.counts || {};
  document.querySelector("[data-count=photos]").textContent = String(counts.photos || 0);
  document.querySelector("[data-count=unread]").textContent = String(counts.unreadMessages || 0);
  document.querySelector("[data-count=notes]").textContent = String(counts.notes || 0);
  document.querySelector("[data-count=todos]").textContent = String(counts.openTodos || 0);
  const index = summary.index || {};
  document.querySelector("[data-index]").textContent = `问屿已索引 ${index.ready || 0} 篇，待继续 ${index.pending || 0} 篇。`;
  renderTodos(Array.isArray(summary.todos) ? summary.todos : []);
  renderActivity(Array.isArray(summary.activity) ? summary.activity : []);
}

function renderTodos(items) {
  todoList.replaceChildren();
  todoEmpty.hidden = items.length !== 0;
  for (const todo of items) {
    const row = document.createElement("div");
    row.className = "todo-row";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = todo.done === true;
    check.setAttribute("aria-label", "完成");
    check.addEventListener("change", () => saveTodo(todo.id, { done: check.checked }));
    const text = document.createElement("input");
    text.type = "text";
    text.value = todo.text || "";
    text.maxLength = 200;
    text.addEventListener("change", () => saveTodo(todo.id, { text: text.value }));
    const due = document.createElement("input");
    due.type = "date";
    due.value = todo.dueOn || "";
    due.setAttribute("aria-label", "截止日期");
    due.addEventListener("change", () => saveTodo(todo.id, { dueOn: due.value }));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger";
    remove.textContent = "删除";
    remove.addEventListener("click", () => {
      pendingDelete = todo.id;
      if (!deletionRequest(todo.id, false).ok) confirmDialog.showModal();
    });
    row.append(check, text, due, remove);
    todoList.append(row);
  }
}

function renderActivity(items) {
  activityList.replaceChildren();
  activityEmpty.hidden = items.length !== 0;
  for (const item of items) {
    const link = document.createElement("a");
    link.className = "letter-row";
    link.href = item.href || "/private/";
    const title = document.createElement("strong");
    title.textContent = `${item.action || ""} ${item.title || ""}`.trim();
    const when = document.createElement("span");
    when.textContent = typeof item.at === "string" ? item.at.slice(0, 16).replace("T", " ") : "";
    link.append(title, when);
    activityList.append(link);
  }
}

async function load() {
  const response = await api("/api/v1/desk-home");
  if (response.status === 401) {
    window.location.assign("/private/login?next=/private/");
    return;
  }
  if (!response.ok) return;
  paint(await response.json());
}

async function saveTodo(id, patch) {
  const response = await api(`/api/v1/desk-todos/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  if (response.ok) await load();
}

if (await ensureSession()) {
  renderNav("overview");
  bindLogout();
  await load();
  document.querySelector("[data-todo-form]").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = document.querySelector("[data-todo-text]");
    const due = document.querySelector("[data-todo-due]");
    const response = await api("/api/v1/desk-todos", {
      method: "POST",
      body: JSON.stringify({ text: text.value, dueOn: due.value || null }),
    });
    if (!response.ok) return;
    text.value = "";
    due.value = "";
    await load();
  });
  document.querySelector("#confirm-cancel").addEventListener("click", () => confirmDialog.close());
  document.querySelector("#confirm-ok").addEventListener("click", async () => {
    if (!deletionRequest(pendingDelete, true).ok) return;
    const response = await api(`/api/v1/desk-todos/${encodeURIComponent(pendingDelete)}`, { method: "DELETE" });
    confirmDialog.close();
    pendingDelete = "";
    if (response.status === 204) await load();
  });
}
