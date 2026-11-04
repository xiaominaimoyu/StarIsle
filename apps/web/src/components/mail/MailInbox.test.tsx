// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MailDetail, MailListResponse, MailSummary } from "@star-isle/contracts";

const api = vi.hoisted(() => ({ listMail: vi.fn(), fetchMailDetail: vi.fn(), markMailRead: vi.fn() }));
vi.mock("@/lib/api/modules", () => api);
vi.mock("@/stores/session", () => ({ getCsrf: () => "synthetic-csrf" }));
import MailInbox from "./MailInbox";

let root: Root | null;
let container: HTMLDivElement;
let poll: () => void;

function mail(id: string, read = true): MailSummary {
  return { id, subject: id, from: "synthetic@example.invalid", receivedAt: "2026-11-04T00:00:00Z", read };
}

function detail(id: string, read = true): MailDetail {
  return { ...mail(id, read), to: "owner@example.invalid", textBody: id + " body", htmlSummary: null, attachments: [] };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function button(label: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button")).find((candidate) => candidate.textContent?.trim() === label);
  expect(found, label).toBeDefined();
  return found!;
}

function rows(): string[] {
  return Array.from(container.querySelectorAll('[data-testid="mail-item"]')).map((row) => row.querySelector("span span")!.textContent!.trim());
}

function open(id: string) {
  const item = Array.from(container.querySelectorAll<HTMLButtonElement>('[data-testid="mail-item"]'))
    .find((candidate) => candidate.textContent?.includes(id));
  expect(item, id).toBeDefined();
  item!.click();
}

async function mount() {
  await act(async () => root!.render(createElement(MailInbox)));
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.spyOn(window, "setInterval").mockImplementation((callback) => {
    poll = callback as () => void;
    return 1 as unknown as ReturnType<typeof window.setInterval>;
  });
  vi.spyOn(window, "clearInterval").mockImplementation(() => {});
  api.markMailRead.mockResolvedValue(undefined);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("MailInbox recoverable list and reader", () => {
  it("keeps rows through a page failure, retries the same cursor and blocks duplicate page requests", async () => {
    const page = deferred<MailListResponse>();
    api.listMail.mockResolvedValueOnce({ items: [mail("first")], nextCursor: "page-2" })
      .mockReturnValueOnce(page.promise)
      .mockResolvedValueOnce({ items: [mail("first"), mail("second")], nextCursor: null });
    await mount();
    const more = button("加载更多");
    await act(async () => { more.click(); more.click(); });
    expect(api.listMail).toHaveBeenCalledTimes(2);
    expect(rows()).toEqual(["first"]);
    await act(async () => page.reject(new Error("synthetic page failure")));
    expect(rows()).toEqual(["first"]);
    expect(container.textContent).toContain("更多邮件加载失败");
    await act(async () => button("重试加载更多").click());
    expect(api.listMail.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, "page-2", "page-2"]);
    expect(rows()).toEqual(["first", "second"]);
  });

  it.each(["manual", "poll"] as const)("keeps previously loaded mail through a failed %s refresh", async (source) => {
    const refresh = deferred<MailListResponse>();
    api.listMail.mockResolvedValueOnce({ items: [mail("first")], nextCursor: null })
      .mockReturnValueOnce(refresh.promise);
    await mount();
    await act(async () => { if (source === "manual") button("刷新").click(); else poll(); });
    expect(rows()).toEqual(["first"]);
    await act(async () => refresh.reject(new Error("synthetic refresh failure")));
    expect(rows()).toEqual(["first"]);
    expect(container.textContent).toContain("邮件列表加载失败");
    expect(button("重试刷新")).toBeDefined();
  });

  it("can leave a pending reader without marking or rendering its late result", async () => {
    const first = deferred<MailDetail>();
    api.listMail.mockResolvedValueOnce({ items: [mail("first", false), mail("second")], nextCursor: null });
    api.fetchMailDetail.mockReturnValueOnce(first.promise).mockResolvedValueOnce(detail("second"));
    await mount();
    await act(async () => open("first"));
    button("← 返回列表").focus();
    await act(async () => button("← 返回列表").click());
    expect(container.firstElementChild?.children[0].classList.contains("hidden")).toBe(false);
    expect(document.activeElement).toBe(container.querySelector('[data-testid="mail-item"]'));
    // A result arriving after returning must not trigger a write while outside the reader.
    await act(async () => first.resolve(detail("first", false)));
    expect(api.markMailRead).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("first body");
    await act(async () => open("second"));
    expect(container.textContent).toContain("second body");
  });

  it("keeps a return-to-list action when the selected detail request fails", async () => {
    api.listMail.mockResolvedValueOnce({ items: [mail("first"), mail("second")], nextCursor: null });
    api.fetchMailDetail.mockRejectedValueOnce(new Error("synthetic detail failure"))
      .mockResolvedValueOnce(detail("second"));
    await mount();
    await act(async () => open("first"));
    expect(container.textContent).toContain("邮件内容加载失败");
    await act(async () => button("← 返回列表").click());
    expect(container.firstElementChild?.children[0].classList.contains("hidden")).toBe(false);
    await act(async () => open("second"));
    expect(container.textContent).toContain("second body");
  });

  it("shows fetched body without waiting for mark-read and exposes an independent read retry", async () => {
    const marking = deferred<void>();
    api.listMail.mockResolvedValueOnce({ items: [mail("first", false)], nextCursor: null });
    api.fetchMailDetail.mockResolvedValueOnce(detail("first", false));
    api.markMailRead.mockReturnValueOnce(marking.promise).mockResolvedValueOnce(undefined);
    await mount();
    await act(async () => open("first"));
    expect(container.textContent).toContain("first body");
    expect(api.markMailRead).toHaveBeenCalledOnce();
    await act(async () => marking.reject(new Error("synthetic read failure")));
    expect(container.textContent).toContain("first body");
    expect(container.textContent).toContain("未读状态已恢复");
    await act(async () => button("重试标记已读").click());
    expect(api.markMailRead).toHaveBeenCalledTimes(2);
    expect(container.textContent).not.toContain("未读状态已恢复");
  });

  it("keeps the current reader retry when another mail has a late failure, while rolling back both rows", async () => {
    const firstMark = deferred<void>();
    api.listMail.mockResolvedValueOnce({ items: [mail("first", false), mail("second", false)], nextCursor: null });
    api.fetchMailDetail.mockResolvedValueOnce(detail("first", false)).mockResolvedValueOnce(detail("second", false));
    api.markMailRead.mockReturnValueOnce(firstMark.promise)
      .mockRejectedValueOnce(new Error("synthetic second read failure"))
      .mockResolvedValueOnce(undefined);
    await mount();
    await act(async () => open("first"));
    await act(async () => button("← 返回列表").click());
    await act(async () => open("second"));
    expect(button("重试标记已读")).toBeDefined();
    await act(async () => firstMark.reject(new Error("synthetic first read failure")));
    expect(container.textContent).toContain("second body");
    expect(button("重试标记已读")).toBeDefined();
    expect(container.querySelectorAll('[aria-label="未读"]')).toHaveLength(2);
    await act(async () => button("重试标记已读").click());
    expect(api.markMailRead.mock.calls.map(([id]) => id)).toEqual(["first", "second", "second"]);
    expect(container.querySelectorAll('[aria-label="未读"]')).toHaveLength(1);
    expect(container.textContent).not.toContain("未读状态已恢复");
  });

  it("keeps an appended cursor and rows when an older head refresh settles later", async () => {
    const refresh = deferred<MailListResponse>();
    const older = deferred<MailListResponse>();
    api.listMail.mockResolvedValueOnce({ items: [mail("second")], nextCursor: "older" })
      .mockReturnValueOnce(refresh.promise).mockReturnValueOnce(older.promise)
      .mockResolvedValueOnce({ items: [], nextCursor: null });
    await mount();
    await act(async () => button("刷新").click());
    await act(async () => button("加载更多").click());
    await act(async () => older.resolve({ items: [mail("first")], nextCursor: "oldest" }));
    await act(async () => refresh.resolve({ items: [mail("third"), mail("second")], nextCursor: "obsolete-head" }));
    expect(rows()).toEqual(["third", "second", "first"]);
    await act(async () => button("加载更多").click());
    expect(api.listMail.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, undefined, "older", "oldest"]);
  });

  it("retries the failed cursor when a concurrent head refresh completes after the page failure", async () => {
    const refresh = deferred<MailListResponse>();
    const older = deferred<MailListResponse>();
    api.listMail.mockResolvedValueOnce({ items: [mail("second")], nextCursor: "older" })
      .mockReturnValueOnce(refresh.promise).mockReturnValueOnce(older.promise)
      .mockResolvedValueOnce({ items: [mail("first")], nextCursor: null });
    await mount();
    await act(async () => button("刷新").click());
    await act(async () => button("加载更多").click());
    await act(async () => older.reject(new Error("synthetic page failure")));
    await act(async () => refresh.resolve({ items: [mail("third"), mail("second")], nextCursor: "new-head-cursor" }));
    expect(rows()).toEqual(["third", "second"]);
    await act(async () => button("重试加载更多").click());
    expect(api.listMail.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, undefined, "older", "older"]);
    expect(rows()).toEqual(["third", "second", "first"]);
  });
});
