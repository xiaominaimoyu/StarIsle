/** 私人书桌里可单测的导航、分页和删除规则。不访问网络或 DOM。 */

export const PRIVATE_NAV = [
  { id: "album", label: "相册", href: "/private/album", enabled: true },
  { id: "messages", label: "来信", href: "/private/messages", enabled: true },
  { id: "notes", label: "手记", href: "/private/notes", enabled: true },
  { id: "ask", label: "问屿", enabled: false, later: "PR4" },
  { id: "overview", label: "总览", enabled: false, later: "PR5" },
];

export const PHOTO_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
export const PHOTO_MAX_BYTES = 20 * 1024 * 1024;
export const ALLOWED_NEXT = new Set(["/private", "/private/", "/private/album", "/private/messages", "/private/notes"]);

export function safeNext(value) {
  return typeof value === "string" && ALLOWED_NEXT.has(value) ? value : "/private/album";
}

export function photoFileIssue(file) {
  if (!file || !PHOTO_TYPES.has(file.type)) return "type";
  if (typeof file.size !== "number" || file.size < 1 || file.size > PHOTO_MAX_BYTES) return "size";
  return null;
}

export function mergePhotos(current, incoming) {
  const seen = new Set(current.map((photo) => photo.id));
  const next = current.slice();
  for (const photo of incoming) {
    if (!photo || typeof photo.id !== "string" || photo.id === "" || seen.has(photo.id)) continue;
    seen.add(photo.id);
    next.push(photo);
  }
  return next;
}

export function removePhoto(current, id) {
  return current.filter((photo) => photo.id !== id);
}

export function deletionRequest(photoId, confirmed) {
  if (typeof photoId !== "string" || photoId === "") return { ok: false, reason: "missing" };
  if (confirmed !== true) return { ok: false, reason: "confirm" };
  return { ok: true, photoId };
}

export function photoLabel(photo) {
  const raw = photo && typeof photo.captureAt === "string" ? photo.captureAt : "";
  const day = raw.slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(day)) return `照片 ${day}`;
  return "照片";
}

export function originalUrl(photoId) {
  return `/api/v1/photos/${encodeURIComponent(photoId)}/original`;
}

export function noteSaveLabel(state) {
  if (state === "dirty") return "未保存";
  if (state === "saving") return "正在保存";
  if (state === "error") return "保存失败";
  return "已保存";
}

export function photoEmbed(photoId) {
  if (typeof photoId !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(photoId)) return "";
  return `![](photo:${photoId})`;
}

export function parseTags(raw) {
  if (typeof raw !== "string") return [];
  const seen = new Set();
  const tags = [];
  for (const part of raw.split(/[,，\s]+/)) {
    const tag = part.trim().toLocaleLowerCase();
    if (!tag || tag.length > 24 || seen.has(tag)) continue;
    seen.add(tag);
    tags.push(tag);
    if (tags.length === 12) break;
  }
  return tags;
}

export function diaryCells(year, month) {
  if (!Number.isInteger(year) || !Number.isInteger(month) || month < 1 || month > 12) return [];
  const start = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const cells = [];
  for (let index = 0; index < start; index += 1) cells.push(null);
  for (let day = 1; day <= days; day += 1) {
    cells.push(`${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`);
  }
  return cells;
}
