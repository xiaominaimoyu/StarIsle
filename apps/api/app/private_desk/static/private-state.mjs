/** 私人书桌里可单测的导航、分页和删除规则。不访问网络或 DOM。 */

export const PRIVATE_NAV = [
  { id: "album", label: "相册", href: "/private/album", enabled: true },
  { id: "messages", label: "来信", enabled: false, later: "PR2" },
  { id: "notes", label: "手记", enabled: false, later: "PR3" },
  { id: "ask", label: "问屿", enabled: false, later: "PR4" },
  { id: "overview", label: "总览", enabled: false, later: "PR5" },
];

export const PHOTO_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
export const PHOTO_MAX_BYTES = 20 * 1024 * 1024;
export const ALLOWED_NEXT = new Set(["/private", "/private/", "/private/album"]);

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
