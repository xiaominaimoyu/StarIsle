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
let epoch = 0;
let timer = 0;
let searchTimer = 0;
let query = "";
const today = new Date();
let viewYear = today.getFullYear();
let viewMonth = today.getMonth() + 1;
const marked = new Map();

function setSave(state) {
  saveStatus.textContent = noteSaveLabel(state);
  saveStatus.dataset.state = state;
}

function formPayload() {
  const title = titleInput.value.trim() || "未命名";
  return {
    title,
    body: bodyInput.value,
    tags: parseTags(tagsInput.value),
  };
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

async function loadNote(id) {
  const response = await api(`/api/v1/notes/${encodeURIComponent(id)}`);
  if (!response.ok) return;
  showNote(await response.json());
  renderList();
}

async function refreshPreview() {
  const response = await api("/api/v1/notes/preview", {
    method: "POST",
    body: JSON.stringify({ body: bodyInput.value }),
  });
  if (!response.ok) return;
  const body = await response.json();
  preview.innerHTML = typeof body.html === "string" ? body.html : "";
}

async function save(started) {
  const payload = formPayload();
  setSave("saving");
  const creating = !current || !current.id;
  const response = await api(creating ? "/api/v1/notes" : `/api/v1/notes/${encodeURIComponent(current.id)}`, {
    method: creating ? "POST" : "PATCH",
    body: JSON.stringify(payload),
  });
  if (started !== epoch) return;
  if (!response.ok) {
    setSave("error");
    return;
  }
  current = await response.json();
  preview.innerHTML = current.html || "";
  setSave("saved");
  await loadList();
}

function scheduleSave() {
  epoch += 1;
  const started = epoch;
  setSave("dirty");
  clearTimeout(timer);
  timer = setTimeout(() => {
    save(started);
    refreshPreview();
  }, 700);
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
  const response = await api(`/api/v1/notes/diary/${date}`, { method: "PUT" });
  if (!response.ok) {
    setSave("error");
    return;
  }
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

if (await ensureSession()) {
  renderNav("notes");
  bindLogout();
  setSave("saved");
  await loadList();
  document.querySelector("[data-new]").addEventListener("click", () => {
    clearTimeout(timer);
    epoch += 1;
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
  document.querySelector("[data-delete]").addEventListener("click", () => {
    if (!current || !current.id) return;
    if (!deletionRequest(current.id, false).ok) confirmDialog.showModal();
  });
  document.querySelector("#confirm-cancel").addEventListener("click", () => confirmDialog.close());
  document.querySelector("#confirm-ok").addEventListener("click", async () => {
    if (!current || !deletionRequest(current.id, true).ok) return;
    const response = await api(`/api/v1/notes/${encodeURIComponent(current.id)}`, { method: "DELETE" });
    confirmDialog.close();
    if (response.status === 204) {
      showNote(null);
      await loadList();
      if (mode === "diary") await loadMonth();
    } else {
      setSave("error");
    }
  });
}
