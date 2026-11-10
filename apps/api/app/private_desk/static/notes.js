import { deletionRequest, diaryCells, noteSaveLabel, parseTags, photoEmbed } from "./private-state.mjs";
import { bindLogout, csrfToken, ensureSession, renderNav } from "./desk.js";

const list = document.querySelector("[data-list]");
const empty = document.querySelector("[data-empty]");
const search = document.querySelector("[data-search]");
const calendar = document.querySelector("[data-calendar]");
const monthBar = document.querySelector("[data-month-bar]");
const monthLabel = document.querySelector("[data-month-label]");
const titleInput = document.querySelector("[data-title]");
const tagsInput = document.querySelector("[data-tags]");
const bodyInput = document.querySelector("[data-body]");
const preview = document.querySelector("[data-preview]");
const saveStatus = document.querySelector("[data-save]");
const picker = document.querySelector("#photo-picker");
const confirmDialog = document.querySelector("#confirm-delete");

let mode = "list";
let notes = [];
let current = null;
let generation = 0;
let timer = 0;
let pending = null;
let searchTimer = 0;
let query = "";
let flushTail = Promise.resolve(true);
const today = new Date();
let viewYear = today.getFullYear();
let viewMonth = today.getMonth() + 1;
const marked = new Map();

function setSave(state, detail) {
  saveStatus.dataset.state = state;
  saveStatus.textContent = detail || noteSaveLabel(state);
}

function formPayload() {
  return {
    title: titleInput.value.trim() || "未命名",
    body: bodyInput.value,
    tags: parseTags(tagsInput.value),
  };
}

function hasUnsaved() {
  return Boolean(
    pending
    || timer
    || saveStatus.dataset.state === "dirty"
    || saveStatus.dataset.state === "saving"
    || saveStatus.dataset.state === "error",
  );
}

function showNote(note) {
  current = note;
  titleInput.value = note ? note.title : "";
  tagsInput.value = note && Array.isArray(note.tags) ? note.tags.join(" ") : "";
  bodyInput.value = note ? note.body || "" : "";
  preview.innerHTML = note && typeof note.html === "string" ? note.html : "";
  setSave("saved");
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") {
    headers["X-CSRF-Token"] = await csrfToken();
  }
  if (options.body) headers["Content-Type"] = "application/json";
  return fetch(path, { credentials: "same-origin", ...options, headers });
}

function renderList() {
  list.hidden = mode !== "list";
  empty.hidden = mode !== "list" || notes.length !== 0;
  list.replaceChildren();
  if (mode !== "list") return;
  for (const note of notes) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "letter-row";
    if (current && current.id === note.id) button.classList.add("is-current");
    const title = document.createElement("strong");
    title.textContent = note.title || "未命名";
    const meta = document.createElement("span");
    meta.textContent = note.diaryOn || (note.tags || []).join(" ");
    button.append(title, meta);
    button.addEventListener("click", () => loadNote(note.id));
    list.append(button);
  }
}

async function loadList() {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  const response = await api(`/api/v1/notes?${params.toString()}`);
  if (response.status === 401) {
    window.location.assign("/private/login?next=/private/notes");
    return;
  }
  const body = response.ok ? await response.json() : { items: [] };
  notes = Array.isArray(body.items) ? body.items : [];
  renderList();
}

async function persist(job) {
  const creating = !job.noteId;
  const payload = creating ? job.payload : { ...job.payload, baseHash: job.baseHash };
  try {
    const response = await api(creating ? "/api/v1/notes" : `/api/v1/notes/${encodeURIComponent(job.noteId)}`, {
      method: creating ? "POST" : "PATCH",
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const detail = response.status === 409 ? "这篇刚刚更新过，没有覆盖。" : "保存失败";
      return { ok: false, detail };
    }
    return { ok: true, note: await response.json() };
  } catch {
    return { ok: false, detail: "保存失败" };
  }
}

async function flushOnce() {
  clearTimeout(timer);
  timer = 0;
  if (!pending) return true;
  const job = pending;
  pending = null;
  if (job.generation === generation) setSave("saving");
  const saved = await persist(job);
  if (!saved.ok) {
    if (!pending || pending.generation !== job.generation) pending = job;
    if (job.generation === generation) setSave("error", saved.detail);
    return false;
  }
  if (pending && pending.generation === job.generation) {
    pending.noteId = saved.note.id;
    pending.baseHash = saved.note.contentHash;
  }
  if (job.generation !== generation) return true;
  current = saved.note;
  if (pending) {
    setSave("dirty");
    return flushOnce();
  }
  preview.innerHTML = saved.note.html || "";
  setSave("saved");
  await loadList();
  return true;
}

function flush() {
  const run = flushTail.then(flushOnce, flushOnce);
  flushTail = run.then(() => true, () => false);
  return run;
}

function scheduleSave() {
  pending = {
    noteId: current && current.id ? current.id : null,
    baseHash: current && current.contentHash ? current.contentHash : null,
    payload: formPayload(),
    generation,
  };
  setSave("dirty");
  clearTimeout(timer);
  timer = setTimeout(() => {
    timer = 0;
    flush();
  }, 700);
}

async function loadNote(id) {
  if (!(await flush())) return;
  if (current && current.id === id) {
    renderList();
    return;
  }
  const response = await api(`/api/v1/notes/${encodeURIComponent(id)}`);
  if (!response.ok) return;
  generation += 1;
  showNote(await response.json());
  renderList();
}

async function loadMonth() {
  monthLabel.textContent = `${viewYear}年${viewMonth}月`;
  const month = `${viewYear}-${String(viewMonth).padStart(2, "0")}`;
  const response = await api(`/api/v1/notes/diary?month=${month}`);
  marked.clear();
  if (response.ok) {
    const body = await response.json();
    for (const day of body.days || []) marked.set(day.date, day.id);
  }
  calendar.replaceChildren();
  for (const label of ["日", "一", "二", "三", "四", "五", "六"]) {
    const cell = document.createElement("span");
    cell.className = "calendar-head";
    cell.textContent = label;
    calendar.append(cell);
  }
  for (const date of diaryCells(viewYear, viewMonth)) {
    if (!date) {
      calendar.append(document.createElement("span"));
      continue;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = String(Number(date.slice(-2)));
    if (marked.has(date)) button.classList.add("has-note");
    button.addEventListener("click", () => openDay(date));
    calendar.append(button);
  }
}

async function openDay(date) {
  if (!(await flush())) return;
  const response = await api(`/api/v1/notes/diary/${date}`, { method: "PUT" });
  if (!response.ok) {
    setSave("error", "这一天没有打开。");
    return;
  }
  generation += 1;
  showNote(await response.json());
  await loadMonth();
  await loadList();
}

function setMode(next) {
  mode = next;
  calendar.hidden = next !== "diary";
  monthBar.hidden = next !== "diary";
  if (next === "diary") loadMonth();
  renderList();
}

async function insertPhoto() {
  const response = await api("/api/v1/photos?limit=60");
  const body = response.ok ? await response.json() : { items: [] };
  const grid = picker.querySelector("[data-picker]");
  grid.replaceChildren();
  for (const photo of body.items || []) {
    const button = document.createElement("button");
    button.type = "button";
    const image = document.createElement("img");
    image.alt = "";
    image.src = photo.thumbnailUrl;
    button.append(image);
    button.addEventListener("click", () => {
      const embed = photoEmbed(photo.id);
      if (!embed) return;
      const start = bodyInput.selectionStart ?? bodyInput.value.length;
      const end = bodyInput.selectionEnd ?? start;
      const lead = start > 0 && bodyInput.value[start - 1] !== "\n" ? "\n" : "";
      bodyInput.value = `${bodyInput.value.slice(0, start)}${lead}${embed}\n${bodyInput.value.slice(end)}`;
      picker.close();
      scheduleSave();
    });
    grid.append(button);
  }
  picker.showModal();
}

function guardLeave(href) {
  flush().then((ok) => {
    if (ok) window.location.assign(href);
  });
}

if (await ensureSession()) {
  renderNav("notes");
  bindLogout();
  setSave("saved");
  await loadList();
  window.addEventListener("beforeunload", (event) => {
    if (!hasUnsaved()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
    if (!link || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target === "_blank" || link.origin !== window.location.origin) return;
    if (!hasUnsaved()) return;
    event.preventDefault();
    event.stopPropagation();
    guardLeave(link.href);
  }, true);
  const logout = document.querySelector("[data-logout]");
  logout.addEventListener("click", (event) => {
    if (!hasUnsaved()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    flush().then(async (ok) => {
      if (!ok) return;
      const token = await csrfToken();
      await fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": token },
      });
      window.location.assign("/private/login");
    });
  }, true);
  document.querySelector("[data-new]").addEventListener("click", async () => {
    if (!(await flush())) return;
    generation += 1;
    showNote(null);
    titleInput.focus();
  });
  for (const button of document.querySelectorAll("[data-mode]")) {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  }
  for (const button of document.querySelectorAll("[data-month]")) {
    button.addEventListener("click", () => {
      viewMonth += Number(button.dataset.month);
      if (viewMonth < 1) {
        viewMonth = 12;
        viewYear -= 1;
      }
      if (viewMonth > 12) {
        viewMonth = 1;
        viewYear += 1;
      }
      loadMonth();
    });
  }
  for (const field of [titleInput, tagsInput, bodyInput]) {
    field.addEventListener("input", scheduleSave);
  }
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      query = search.value.trim();
      setMode("list");
      loadList();
    }, 300);
  });
  document.querySelector("[data-photos]").addEventListener("click", insertPhoto);
  document.querySelector("#picker-close").addEventListener("click", () => picker.close());
  document.querySelector("[data-delete]").addEventListener("click", async () => {
    if (!(await flush())) return;
    if (!current || !current.id) return;
    confirmDialog.showModal();
  });
  document.querySelector("#confirm-cancel").addEventListener("click", () => confirmDialog.close());
  document.querySelector("#confirm-ok").addEventListener("click", async () => {
    confirmDialog.close();
    if (!(await flush())) return;
    if (!current || !deletionRequest(current.id, true).ok) return;
    const response = await api(`/api/v1/notes/${encodeURIComponent(current.id)}`, { method: "DELETE" });
    if (response.status === 204) {
      generation += 1;
      showNote(null);
      await loadList();
      if (mode === "diary") await loadMonth();
    } else {
      setSave("error");
    }
  });
}
