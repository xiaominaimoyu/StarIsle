// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DropZone from "./DropZone";

let root: Root | null;
let container: HTMLDivElement;

function deferred() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function selectFiles(count: number) {
  const input = container.querySelector("input")!;
  const files = Array.from({ length: count }, (_, index) => new File(["synthetic"], `upload-${index}.txt`, { type: "text/plain" }));
  Object.defineProperty(input, "files", { configurable: true, value: files });
  act(() => input.dispatchEvent(new Event("change", { bubbles: true })));
}

beforeEach(() => {
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

describe("DropZone upload lifecycle", () => {
  it.each(["resolve", "reject"] as const)("does not start queued writes when an in-flight upload settles after unmount: %s", async (outcome) => {
    const pending = deferred();
    const onUpload = vi.fn(() => pending.promise);
    await act(async () => root!.render(createElement(DropZone, { maxBytes: 1024, maxConcurrency: 1, onUpload })));
    selectFiles(3);
    expect(onUpload).toHaveBeenCalledOnce();

    act(() => root!.unmount());
    root = null;
    await act(async () => {
      if (outcome === "resolve") pending.resolve();
      else pending.reject(new Error("synthetic upload failure"));
    });

    expect(onUpload).toHaveBeenCalledOnce();
  });

  it("keeps at most three uploads in flight and advances the queue exactly once per completed item", async () => {
    const jobs: ReturnType<typeof deferred>[] = [];
    let active = 0;
    let peak = 0;
    const onUpload = vi.fn(async () => {
      const job = deferred();
      jobs.push(job);
      active += 1;
      peak = Math.max(peak, active);
      try { await job.promise; } finally { active -= 1; }
    });
    await act(async () => root!.render(createElement(DropZone, { maxBytes: 1024, maxConcurrency: 6, onUpload })));
    selectFiles(5);
    expect(onUpload).toHaveBeenCalledTimes(3);
    await act(async () => jobs[0].resolve());
    expect(onUpload).toHaveBeenCalledTimes(4);
    await act(async () => jobs[1].resolve());
    expect(onUpload).toHaveBeenCalledTimes(5);
    await act(async () => jobs.slice(2).forEach((job) => job.resolve()));

    expect(active).toBe(0);
    expect(peak).toBe(3);
    expect(container.querySelectorAll("li")).toHaveLength(5);
    expect(container.querySelectorAll('[role="progressbar"]')).toHaveLength(0);
    expect(container.textContent?.match(/✓ 完成/g)).toHaveLength(5);
  });
});
