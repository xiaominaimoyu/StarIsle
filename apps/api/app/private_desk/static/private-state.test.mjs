import assert from "node:assert/strict";
import test from "node:test";

import {
  PRIVATE_NAV,
  deletionRequest,
  diaryCells,
  mergePhotos,
  noteSaveLabel,
  originalUrl,
  parseTags,
  photoEmbed,
  photoFileIssue,
  photoLabel,
  removePhoto,
  safeNext,
} from "./private-state.mjs";

test("album, letters, notes and ask are open while later rooms have no link", () => {
  const open = PRIVATE_NAV.filter((item) => item.enabled);
  assert.deepEqual(open.map((item) => item.id), ["album", "messages", "notes", "ask"]);
  assert.equal(open[3].href, "/private/ask");
  for (const item of PRIVATE_NAV.filter((entry) => !entry.enabled)) {
    assert.equal(item.href, undefined);
    assert.equal(typeof item.later, "string");
  }
  assert.deepEqual(
    PRIVATE_NAV.map((item) => item.id),
    ["album", "messages", "notes", "ask", "overview"],
  );
});

test("next path stays inside the private desk", () => {
  assert.equal(safeNext("/private/album"), "/private/album");
  assert.equal(safeNext("/private/"), "/private/");
  assert.equal(safeNext("/private/messages"), "/private/messages");
  assert.equal(safeNext("/private/notes"), "/private/notes");
  assert.equal(safeNext("/private/ask"), "/private/ask");
  assert.equal(safeNext("https://example.test"), "/private/album");
  assert.equal(safeNext("//example.test"), "/private/album");
  assert.equal(safeNext("/console"), "/private/album");
  assert.equal(safeNext(null), "/private/album");
});

test("photo pages merge without duplicates and deletion needs confirmation", () => {
  const first = [{ id: "a" }, { id: "b" }];
  const merged = mergePhotos(first, [{ id: "b" }, { id: "c" }, { id: "" }, null]);
  assert.deepEqual(merged.map((photo) => photo.id), ["a", "b", "c"]);
  assert.deepEqual(removePhoto(merged, "b").map((photo) => photo.id), ["a", "c"]);
  assert.deepEqual(deletionRequest("a", false), { ok: false, reason: "confirm" });
  assert.deepEqual(deletionRequest("", true), { ok: false, reason: "missing" });
  assert.deepEqual(deletionRequest("a", true), { ok: true, photoId: "a" });
});

test("client checks reject the wrong type or an oversized file before upload", () => {
  assert.equal(photoFileIssue({ type: "image/png", size: 12 }), null);
  assert.equal(photoFileIssue({ type: "text/plain", size: 12 }), "type");
  assert.equal(photoFileIssue({ type: "image/gif", size: 20 * 1024 * 1024 + 1 }), "size");
  assert.equal(photoLabel({ captureAt: "2026-11-10T08:00:00" }), "照片 2026-11-10");
  assert.equal(photoLabel({ captureAt: null }), "照片");
  assert.equal(originalUrl("photo 1"), "/api/v1/photos/photo%201/original");
});

test("notes keep a visible save state and only embed album photo ids", () => {
  assert.equal(noteSaveLabel("dirty"), "未保存");
  assert.equal(noteSaveLabel("saving"), "正在保存");
  assert.equal(noteSaveLabel("saved"), "已保存");
  assert.equal(noteSaveLabel("error"), "保存失败");
  assert.equal(photoEmbed("photo_abc"), "![](photo:photo_abc)");
  assert.equal(photoEmbed("../secret"), "");
  assert.deepEqual(parseTags("星屿, 日记 日记"), ["星屿", "日记"]);
  const cells = diaryCells(2026, 11);
  assert.equal(cells.filter(Boolean).length, 30);
  assert.equal(cells.includes("2026-11-10"), true);
  assert.equal(cells.at(-1), "2026-11-30");
  assert.equal(diaryCells(2026, 13).length, 0);
});
