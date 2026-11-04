// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FileListResponse, FileSummary } from "@star-isle/contracts";

const api = vi.hoisted(() => ({ listFiles: vi.fn(), uploadFile: vi.fn(), deleteFile: vi.fn(), downloadFileUrl: vi.fn() }));
vi.mock("@/lib/api/modules", () => api);
vi.mock("@/stores/session", () => ({ getCsrf: () => "synthetic-csrf" }));
import FileTable from "./FileTable";

let root: Root | null;
let container: HTMLDivElement;

function file(id: string): FileSummary {
  return { id, name: id + ".txt", size: 4, mimeType: "text/plain", createdAt: "2026-11-03T10:00:00Z", tags: [] };
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

function rows(): string[] {
  return Array.from(container.querySelectorAll("tbody tr")).map((row) => row.querySelector("td")!.textContent!);
}

async function mount() {
  await act(async () => root!.render(createElement(FileTable)));
}

async function upload() {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { configurable: true, value: [new File(["demo"], "new.txt", { type: "text/plain" })] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  api.downloadFileUrl.mockImplementation((id: string) => "/synthetic/" + id);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("FileTable list and mutation recovery", () => {
  it("keeps loaded rows after a page failure, retries the same cursor and deduplicates overlaps", async () => {
    api.listFiles.mockResolvedValueOnce({ items: [file("first")], nextCursor: "page-2" })
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce({ items: [file("first"), file("second")], nextCursor: null });
    await mount();
    await act(async () => button("加载更多").click());
    expect(rows()).toEqual(["first.txt"]);
    expect(container.textContent).toContain("更多文件加载失败");
    await act(async () => button("重试加载更多").click());
    expect(api.listFiles.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, "page-2", "page-2"]);
    expect(rows()).toEqual(["first.txt", "second.txt"]);
  });

  it("does not lose a successful upload when an older initial list response arrives", async () => {
    const initial = deferred<FileListResponse>();
    api.listFiles.mockReturnValueOnce(initial.promise);
    api.uploadFile.mockResolvedValueOnce(file("new"));
    await mount();
    await upload();
    await act(async () => initial.resolve({ items: [file("older")], nextCursor: null }));
    expect(rows()).toEqual(["new.txt", "older.txt"]);
  });

  it("does not resurrect a deleted file when an older page response arrives", async () => {
    const older = deferred<FileListResponse>();
    api.listFiles.mockResolvedValueOnce({ items: [file("deleted")], nextCursor: "page-2" })
      .mockReturnValueOnce(older.promise);
    api.deleteFile.mockResolvedValueOnce(undefined);
    await mount();
    await act(async () => button("加载更多").click());
    await act(async () => button("删除").click());
    await act(async () => button("确认删除").click());
    await act(async () => older.resolve({ items: [file("deleted"), file("survivor")], nextCursor: null }));
    expect(rows()).toEqual(["survivor.txt"]);
    expect(api.deleteFile).toHaveBeenCalledOnce();
  });

  it("keeps a confirmed upload visible even if the earlier initial list fails", async () => {
    const initial = deferred<FileListResponse>();
    api.listFiles.mockReturnValueOnce(initial.promise);
    api.uploadFile.mockResolvedValueOnce(file("new"));
    await mount();
    await upload();
    expect(rows()).toEqual(["new.txt"]);
    await act(async () => initial.reject(new Error("temporary")));
    expect(rows()).toEqual(["new.txt"]);
    expect(container.textContent).toContain("文件列表加载失败");
    expect(button("重试")).toBeDefined();
  });

  it("sends only one deletion when confirmation is clicked twice before rendering", async () => {
    const deletion = deferred<void>();
    api.listFiles.mockResolvedValueOnce({ items: [file("deleted")], nextCursor: null });
    api.deleteFile.mockReturnValueOnce(deletion.promise);
    await mount();
    await act(async () => button("删除").click());
    const confirm = button("确认删除");
    await act(async () => { confirm.click(); confirm.click(); });
    expect(api.deleteFile).toHaveBeenCalledOnce();
    await act(async () => deletion.resolve(undefined));
    expect(rows()).toEqual([]);
  });

  it("clears a previous deletion failure when another file is selected", async () => {
    api.listFiles.mockResolvedValueOnce({ items: [file("first"), file("second")], nextCursor: null });
    api.deleteFile.mockRejectedValueOnce(new Error("synthetic deletion failed"));
    await mount();
    await act(async () => button("删除").click());
    await act(async () => button("确认删除").click());
    expect(container.querySelector('[role="dialog"]')?.textContent).toContain("synthetic deletion failed");
    await act(async () => button("取消").click());
    const secondDelete = container.querySelectorAll<HTMLButtonElement>("tbody tr button")[1];
    await act(async () => secondDelete.click());
    expect(container.querySelector('[role="dialog"]')?.textContent).not.toContain("synthetic deletion failed");
  });
});
