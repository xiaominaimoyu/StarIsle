import {
  deletionRequest,
  mergePhotos,
  originalUrl,
  photoFileIssue,
  photoLabel,
  removePhoto,
} from "./private-state.mjs";
import { bindLogout, csrfToken, ensureSession, readJson, renderNav } from "./desk.js";

const grid = document.querySelector("[data-grid]");
const empty = document.querySelector("[data-empty]");
const more = document.querySelector("[data-more]");
const status = document.querySelector("[data-status]");
const fileInput = document.querySelector("#photo-file");
const viewer = document.querySelector("#viewer");
const viewerImage = document.querySelector("#viewer-image");
const confirmDialog = document.querySelector("#confirm-delete");
const confirmText = document.querySelector("#confirm-text");

let photos = [];
let cursor = null;
let pendingId = "";

function note(message, ok) {
  status.hidden = false;
  status.textContent = message;
  status.className = ok ? "ok-note" : "alert";
}

function render() {
  grid.replaceChildren();
  empty.hidden = photos.length !== 0;
  more.hidden = !cursor;
  for (const photo of photos) {
    const card = document.createElement("article");
    card.className = "photo-card";
    const open = document.createElement("button");
    open.type = "button";
    open.className = "ghost";
    const image = document.createElement("img");
    image.src = photo.thumbnailUrl;
    image.alt = photoLabel(photo);
    open.append(image);
    open.addEventListener("click", () => {
      viewerImage.src = originalUrl(photo.id);
      viewerImage.alt = photoLabel(photo);
      viewer.showModal();
    });
    const actions = document.createElement("div");
    actions.className = "photo-actions";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "删除";
    remove.addEventListener("click", () => {
      pendingId = photo.id;
      confirmText.textContent = `删除「${photoLabel(photo)}」？这会从这台电脑的相册里拿掉。`;
      confirmDialog.showModal();
    });
    actions.append(remove);
    card.append(open, actions);
    grid.append(card);
  }
}

async function loadPage(reset) {
  const params = new URLSearchParams({ limit: "24" });
  if (!reset && cursor) params.set("cursor", cursor);
  const response = await fetch(`/api/v1/photos?${params}`, { credentials: "same-origin" });
  if (response.status === 401) {
    window.location.assign("/private/login?next=/private/album");
    return;
  }
  if (!response.ok) {
    note("相册没有载入，请再试一次。", false);
    return;
  }
  const body = await response.json();
  const incoming = Array.isArray(body.items) ? body.items : [];
  photos = reset ? mergePhotos([], incoming) : mergePhotos(photos, incoming);
  cursor = typeof body.nextCursor === "string" ? body.nextCursor : null;
  render();
}

if (await ensureSession()) {
  renderNav("album");
  bindLogout();
  await loadPage(true);

  document.querySelector("[data-upload]").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = "";
    if (!file) return;
    const issue = photoFileIssue(file);
    if (issue === "type") {
      note("只能放入 JPEG、PNG、WEBP 或 GIF。", false);
      return;
    }
    if (issue === "size") {
      note("这张图超过 20 MB。", false);
      return;
    }
    try {
      const token = await csrfToken();
      const form = new FormData();
      form.append("file", file, file.name);
      const response = await fetch("/api/v1/photos", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRF-Token": token,
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: form,
      });
      if (!response.ok) {
        const body = await readJson(response);
        note(body && body.error && body.error.message ? body.error.message : "没有放进去。", false);
        return;
      }
      note("已放入相册。", true);
      cursor = null;
      await loadPage(true);
    } catch {
      note("上传没有完成。", false);
    }
  });

  more.addEventListener("click", () => loadPage(false));
  document.querySelector("#viewer-close").addEventListener("click", () => viewer.close());
  document.querySelector("#confirm-cancel").addEventListener("click", () => {
    pendingId = "";
    confirmDialog.close();
  });
  document.querySelector("#confirm-ok").addEventListener("click", async () => {
    const decision = deletionRequest(pendingId, true);
    pendingId = "";
    confirmDialog.close();
    if (!decision.ok) return;
    try {
      const token = await csrfToken();
      const response = await fetch(`/api/v1/photos/${encodeURIComponent(decision.photoId)}`, {
        method: "DELETE",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": token },
      });
      if (!response.ok && response.status !== 404) {
        note("没有删掉，照片还在。", false);
        return;
      }
      photos = removePhoto(photos, decision.photoId);
      render();
      note("已从相册拿掉。", true);
    } catch {
      note("删除没有完成。", false);
    }
  });
}
