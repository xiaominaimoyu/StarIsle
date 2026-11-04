// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PhotoListResponse, PhotoSummary } from "@star-isle/contracts";

const api = vi.hoisted(() => ({ listPhotos: vi.fn(), uploadPhoto: vi.fn(), deletePhoto: vi.fn() }));
vi.mock("@/lib/api/modules", () => api);
vi.mock("@/stores/session", () => ({ getCsrf: () => "synthetic-csrf" }));
import PhotoTimeline from "./PhotoTimeline";

let root: Root | null;
let container: HTMLDivElement;

function photo(id: string): PhotoSummary {
  return { id, thumbnailUrl: `/synthetic/${id}.png`, captureAt: null, width: 8, height: 6 };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function button(label: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button")).find((candidate) => candidate.textContent?.trim() === label);
  expect(found, label).toBeDefined();
  return found!;
}

function photoIds(): string[] {
  return Array.from(container.querySelectorAll<HTMLImageElement>("img")).map((image) => image.getAttribute("src")!.split("/").pop()!.replace(".png", ""));
}

async function mount() {
  await act(async () => root!.render(createElement(PhotoTimeline)));
}

async function upload(name = "new.png") {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { configurable: true, value: [new File(["demo"], name, { type: "image/png" })] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
}

async function remove(id: string) {
  const image = container.querySelector<HTMLImageElement>(`img[src="/synthetic/${id}.png"]`)!;
  expect(image).not.toBeNull();
  await act(async () => image.parentElement!.querySelector<HTMLButtonElement>("button")!.click());
  await act(async () => button("确认删除").click());
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("PhotoTimeline refresh and pagination recovery", () => {
  it("keeps loaded pages during an upload refresh failure, retries and rescans the changed head cursor", async () => {
    const refresh = deferred<PhotoListResponse>();
    api.listPhotos.mockResolvedValueOnce({ items: [photo("first")], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [photo("second")], nextCursor: "page-3" })
      .mockReturnValueOnce(refresh.promise)
      .mockResolvedValueOnce({ items: [photo("new"), photo("first")], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [photo("second"), photo("third")], nextCursor: null });
    api.uploadPhoto.mockResolvedValueOnce({ id: "new", status: "ready", thumbnailState: "ready" });
    await mount();
    await act(async () => button("加载更多").click());
    await upload();
    expect(photoIds()).toEqual(["first", "second"]);
    expect(container.querySelectorAll('[aria-label="删除这张照片"]')).toHaveLength(2);
    await act(async () => refresh.reject(new Error("synthetic refresh failure")));
    expect(photoIds()).toEqual(["first", "second"]);
    expect(container.textContent).toContain("相册刷新失败");
    await act(async () => button("重试刷新").click());
    expect(photoIds()).toEqual(["new", "first", "second"]);
    await act(async () => button("加载更多").click());
    expect(api.listPhotos.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, "page-2", undefined, undefined, "page-2"]);
    expect(photoIds()).toEqual(["new", "first", "second", "third"]);
  });

  it("can reopen an exhausted cursor reported by a changed head without dropping or duplicating previously loaded pages", async () => {
    api.listPhotos.mockResolvedValueOnce({ items: [photo("first")], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [photo("second")], nextCursor: null })
      .mockResolvedValueOnce({ items: [photo("new"), photo("first")], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [photo("first"), photo("second")], nextCursor: null });
    api.uploadPhoto.mockResolvedValueOnce({ id: "new", status: "ready", thumbnailState: "ready" });
    await mount();
    await act(async () => button("加载更多").click());
    await upload();
    expect(photoIds()).toEqual(["new", "first", "second"]);
    await act(async () => button("加载更多").click());
    expect(api.listPhotos.mock.calls.at(-1)).toEqual(["page-2"]);
    expect(photoIds()).toEqual(["new", "first", "second"]);
    expect(Array.from(container.querySelectorAll("button")).some((item) => item.textContent?.trim() === "加载更多")).toBe(false);
  });

  it("allows newly uploaded older photos beyond a previously exhausted first page to be reached", async () => {
    // One-item pages model the real API boundary: the new photo sorts after the
    // previously complete collection, so a head refresh cannot include it.
    api.listPhotos.mockResolvedValueOnce({ items: [photo("first")], nextCursor: null })
      .mockResolvedValueOnce({ items: [photo("first")], nextCursor: "older-boundary" })
      .mockResolvedValueOnce({ items: [photo("uploaded-older")], nextCursor: null });
    api.uploadPhoto.mockResolvedValueOnce({ id: "uploaded-older", status: "ready", thumbnailState: "ready" });
    await mount();
    await upload();
    expect(photoIds()).toEqual(["first"]);
    await act(async () => button("加载更多").click());
    expect(api.listPhotos.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, undefined, "older-boundary"]);
    expect(photoIds()).toEqual(["first", "uploaded-older"]);
  });

  it("rescans after an upload so a middle-date photo is reachable inside previously loaded pages", async () => {
    api.listPhotos.mockResolvedValueOnce({ items: [photo("first")], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [photo("older"), photo("tail")], nextCursor: "page-4" })
      .mockResolvedValueOnce({ items: [photo("first")], nextCursor: "page-2" })
      .mockImplementationOnce(async (cursor?: string) => cursor === "page-2"
        ? { items: [photo("older"), photo("uploaded-middle")], nextCursor: "page-3" }
        : { items: [], nextCursor: null });
    api.uploadPhoto.mockResolvedValueOnce({ id: "uploaded-middle", status: "ready", thumbnailState: "ready" });
    await mount();
    await act(async () => button("加载更多").click());
    await upload();
    expect(photoIds()).toEqual(["first", "older", "tail"]);
    await act(async () => button("加载更多").click());
    expect(api.listPhotos.mock.calls.at(-1)).toEqual(["page-2"]);
    expect(photoIds()).toEqual(["first", "older", "tail", "uploaded-middle"]);
    expect(new Set(photoIds()).size).toBe(photoIds().length);
  });

  it("keeps surviving pages through delete refresh failure and filters deleted IDs from a retried refresh", async () => {
    const refresh = deferred<PhotoListResponse>();
    api.listPhotos.mockResolvedValueOnce({ items: [photo("first")], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [photo("deleted"), photo("survivor")], nextCursor: "page-3" })
      .mockReturnValueOnce(refresh.promise)
      .mockResolvedValueOnce({ items: [photo("first"), photo("deleted")], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [photo("older")], nextCursor: null });
    api.deletePhoto.mockResolvedValueOnce(undefined);
    await mount();
    await act(async () => button("加载更多").click());
    await remove("deleted");
    expect(photoIds()).toEqual(["first", "survivor"]);
    await act(async () => refresh.reject(new Error("synthetic refresh failure")));
    expect(photoIds()).toEqual(["first", "survivor"]);
    await act(async () => button("重试刷新").click());
    expect(photoIds()).toEqual(["first", "survivor"]);
    await act(async () => button("加载更多").click());
    expect(api.listPhotos.mock.calls.at(-1)).toEqual(["page-3"]);
    expect(photoIds()).toEqual(["first", "survivor", "older"]);
  });

  it("discards an obsolete append after deletion, unlocks its original cursor and keeps the current refresh busy", async () => {
    const append = deferred<PhotoListResponse>();
    const refresh = deferred<PhotoListResponse>();
    api.listPhotos.mockResolvedValueOnce({ items: [photo("first"), photo("deleted")], nextCursor: "page-2" })
      .mockReturnValueOnce(append.promise)
      .mockReturnValueOnce(refresh.promise)
      .mockResolvedValueOnce({ items: [photo("deleted"), photo("older")], nextCursor: null });
    api.deletePhoto.mockResolvedValueOnce(undefined);
    await mount();
    await act(async () => button("加载更多").click());
    await remove("deleted");
    expect(photoIds()).toEqual(["first"]);
    await act(async () => append.resolve({ items: [photo("deleted"), photo("obsolete")], nextCursor: "page-3" }));
    expect(photoIds()).toEqual(["first"]);
    expect(button("加载更多").disabled).toBe(true);
    await act(async () => refresh.reject(new Error("synthetic refresh failure")));
    expect(button("加载更多").disabled).toBe(false);
    await act(async () => button("加载更多").click());
    expect(api.listPhotos.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, "page-2", undefined, "page-2"]);
    expect(photoIds()).toEqual(["first", "older"]);
  });

  it("does not let an older upload refresh hide a newer refresh or overwrite its results", async () => {
    const older = deferred<PhotoListResponse>();
    const latest = deferred<PhotoListResponse>();
    api.listPhotos.mockResolvedValueOnce({ items: [photo("first")], nextCursor: null })
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(latest.promise);
    api.uploadPhoto.mockResolvedValueOnce({ id: "new-1", status: "ready", thumbnailState: "ready" })
      .mockResolvedValueOnce({ id: "new-2", status: "ready", thumbnailState: "ready" });
    await mount();
    await upload("new-1.png");
    await upload("new-2.png");
    expect(photoIds()).toEqual(["first"]);
    await act(async () => older.resolve({ items: [photo("new-1"), photo("first")], nextCursor: null }));
    expect(photoIds()).toEqual(["first"]);
    expect(container.textContent).toContain("正在刷新相册");
    await act(async () => latest.resolve({ items: [photo("new-2"), photo("new-1"), photo("first")], nextCursor: null }));
    expect(photoIds()).toEqual(["new-2", "new-1", "first"]);
    expect(container.textContent).not.toContain("正在刷新相册");
  });

  it("keeps existing photos after an append failure and retries the same cursor", async () => {
    api.listPhotos.mockResolvedValueOnce({ items: [photo("first")], nextCursor: "page-2" })
      .mockRejectedValueOnce(new Error("synthetic append failure"))
      .mockResolvedValueOnce({ items: [photo("first"), photo("second")], nextCursor: null });
    await mount();
    await act(async () => button("加载更多").click());
    expect(photoIds()).toEqual(["first"]);
    await act(async () => button("重试加载更多").click());
    expect(api.listPhotos.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, "page-2", "page-2"]);
    expect(photoIds()).toEqual(["first", "second"]);
  });

  it("does not refresh after a pending upload completes following unmount", async () => {
    const uploadResult = deferred<{ id: string; status: "ready"; thumbnailState: "ready" }>();
    api.listPhotos.mockResolvedValueOnce({ items: [photo("first")], nextCursor: null });
    api.uploadPhoto.mockReturnValueOnce(uploadResult.promise);
    await mount();
    await upload();
    await act(async () => root!.unmount());
    root = null;
    await act(async () => uploadResult.resolve({ id: "new", status: "ready", thumbnailState: "ready" }));
    expect(api.listPhotos).toHaveBeenCalledOnce();
  });
});
