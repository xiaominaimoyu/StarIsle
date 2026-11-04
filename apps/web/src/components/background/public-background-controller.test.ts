// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { URL as FileURL } from "node:url";
import { transformSync } from "esbuild";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clampNumber, parsePublicBackgroundSettings } from "@/lib/public-ui";

const component = readFileSync(new FileURL("./PublicBackgroundController.astro", import.meta.url), "utf8");
const script = component.match(/<script>([\s\S]*?)<\/script>/)?.[1];
if (!script) throw new Error("PublicBackgroundController client script is missing");
const clientCode = transformSync(script, { loader: "ts", format: "cjs" }).code;
const compress = vi.fn();
const saveBlob = vi.fn();
const clearBlob = vi.fn();
let keyHandler: EventListenerOrEventListenerObject | undefined;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

function settings() {
  return JSON.parse(localStorage.getItem("star-isle:background-settings")!);
}

function upload(name = "synthetic.png") {
  const input = document.querySelector<HTMLInputElement>("[data-background-file]")!;
  Object.defineProperty(input, "files", { configurable: true, value: [new File(["synthetic"], name)] });
  input.dispatchEvent(new Event("change"));
}

function reset() {
  document.querySelector<HTMLButtonElement>("[data-background-reset]")!.click();
}

function uploader() {
  return document.querySelector<HTMLButtonElement>("[data-background-file-trigger]")!;
}

beforeEach(() => {
  vi.resetAllMocks();
  keyHandler = undefined;
  saveBlob.mockResolvedValue(undefined);
  clearBlob.mockResolvedValue(undefined);
  localStorage.removeItem("star-isle:background-settings");
  document.body.innerHTML = `
    <div data-public-background-controller>
      <button data-background-open>打开</button>
      <div data-public-modal data-background-dialog hidden><div data-background-panel tabindex="-1"></div></div>
      <div data-background-message hidden></div>
      <input data-background-file type="file" />
      <button data-background-file-trigger>上传</button>
      <button data-background-preset="/presets/new.svg">预设</button>
      <button data-background-reset>恢复默认</button>
    </div>`;
  const listenerSpy = vi.spyOn(document, "addEventListener");
  new Function("require", clientCode)((name: string) => {
    if (name === "./public-background-storage") return { savePublicBackgroundBlob: saveBlob, clearPublicBackgroundBlob: clearBlob };
    if (name === "@/lib/image") return { compressImage: compress };
    if (name === "@/lib/public-ui") return { clampNumber, parsePublicBackgroundSettings };
    throw new Error("Unexpected background script import");
  });
  keyHandler = listenerSpy.mock.calls.find(([type]) => type === "keydown")?.[1] ?? undefined;
});

afterEach(() => {
  if (keyHandler) document.removeEventListener("keydown", keyHandler);
  document.body.innerHTML = "";
  localStorage.removeItem("star-isle:background-settings");
  vi.restoreAllMocks();
});

describe("public background selection ordering", () => {
  it("keeps a reset while an old compression finishes without saving that obsolete image", async () => {
    const pending = deferred<{ ok: true; blob: Blob }>();
    compress.mockReturnValueOnce(pending.promise);
    upload();
    expect(uploader().disabled).toBe(true);
    reset();
    expect(uploader().disabled).toBe(false);
    pending.resolve({ ok: true, blob: new Blob(["old"]) });
    await Promise.resolve();
    await Promise.resolve();

    expect(saveBlob).not.toHaveBeenCalled();
    expect(settings().custom).toBe(false);
    expect(settings().url).toBeNull();
  });

  it("clears storage after an already-running obsolete save, without restoring its UI", async () => {
    const saving = deferred<void>();
    let stored: Blob | null = null;
    compress.mockResolvedValueOnce({ ok: true, blob: new Blob(["old"]) });
    saveBlob.mockImplementationOnce(async (blob: Blob) => { await saving.promise; stored = blob; });
    clearBlob.mockImplementationOnce(async () => { stored = null; });
    upload();
    await vi.waitFor(() => expect(saveBlob).toHaveBeenCalledOnce());
    reset();
    expect(clearBlob).not.toHaveBeenCalled();
    saving.resolve();
    await vi.waitFor(() => expect(clearBlob).toHaveBeenCalledOnce());

    expect(stored).toBeNull();
    expect(settings().custom).toBe(false);
  });

  it("saves a newer image after a pending reset clear finishes", async () => {
    const clearing = deferred<void>();
    const blob = new Blob(["new"]);
    clearBlob.mockReturnValueOnce(clearing.promise);
    compress.mockResolvedValueOnce({ ok: true, blob });
    reset();
    await vi.waitFor(() => expect(clearBlob).toHaveBeenCalledOnce());
    upload();
    await Promise.resolve();
    await Promise.resolve();
    expect(saveBlob).not.toHaveBeenCalled();
    clearing.resolve();
    await vi.waitFor(() => expect(saveBlob).toHaveBeenCalledWith(blob));
    await vi.waitFor(() => expect(settings().custom).toBe(true));
  });

  it("does not replace a newer preset when an obsolete save rejects", async () => {
    const saving = deferred<void>();
    compress.mockResolvedValueOnce({ ok: true, blob: new Blob(["old"]) });
    saveBlob.mockImplementationOnce(async () => { await saving.promise; throw new Error("synthetic storage error"); });
    upload();
    await vi.waitFor(() => expect(saveBlob).toHaveBeenCalledOnce());
    document.querySelector<HTMLButtonElement>("[data-background-preset]")!.click();
    saving.resolve();
    await vi.waitFor(() => expect(uploader().disabled).toBe(false));

    expect(settings().url).toBe("/presets/new.svg");
    expect(settings().custom).toBe(false);
    expect(document.querySelector<HTMLElement>("[data-background-message]")!.hidden).toBe(true);
  });

  it("does not let an obsolete completion enable or clear a newer upload", async () => {
    const first = deferred<{ ok: true; blob: Blob }>();
    const second = deferred<{ ok: true; blob: Blob }>();
    const latestBlob = new Blob(["new"]);
    compress.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    upload("first.png");
    reset();
    upload("second.png");
    first.resolve({ ok: true, blob: new Blob(["old"]) });
    await Promise.resolve();
    await Promise.resolve();
    expect(uploader().disabled).toBe(true);
    second.resolve({ ok: true, blob: latestBlob });
    await vi.waitFor(() => expect(saveBlob).toHaveBeenCalledWith(latestBlob));
    await vi.waitFor(() => expect(uploader().disabled).toBe(false));
    expect(saveBlob).toHaveBeenCalledOnce();
    expect(settings().custom).toBe(true);
  });

  it("reports a compression rejection and enables a new attempt", async () => {
    compress.mockRejectedValueOnce(new Error("synthetic processing error"));
    upload();
    await vi.waitFor(() => expect(uploader().disabled).toBe(false));
    const message = document.querySelector<HTMLElement>("[data-background-message]")!;
    expect(message.hidden).toBe(false);
    expect(message.textContent).toContain("图片处理失败");
    expect(saveBlob).not.toHaveBeenCalled();
  });

  it("reports a failed clear without blocking a later save", async () => {
    clearBlob.mockRejectedValueOnce(new Error("synthetic clear error"));
    reset();
    await vi.waitFor(() => expect(document.querySelector<HTMLElement>("[data-background-message]")!.hidden).toBe(false));
    expect(settings().custom).toBe(false);
    expect(settings().url).toBeNull();
    const blob = new Blob(["retry"]);
    compress.mockResolvedValueOnce({ ok: true, blob });
    upload();
    await vi.waitFor(() => expect(saveBlob).toHaveBeenCalledWith(blob));
    await vi.waitFor(() => expect(settings().custom).toBe(true));
    expect(uploader().disabled).toBe(false);
  });
});
