// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { URL as FileURL } from "node:url";
import { transformSync } from "esbuild";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parsePublicBackgroundSettings, type PublicBackgroundSettings } from "@/lib/public-ui";

const component = readFileSync(new FileURL("./PublicBackgroundLayer.astro", import.meta.url), "utf8");
const script = component.match(/<script>([\s\S]*?)<\/script>/)?.[1];
if (!script) throw new Error("PublicBackgroundLayer client script is missing");
const clientCode = transformSync(script, { loader: "ts", format: "cjs" }).code;
const loadBlob = vi.fn();
const createUrl = vi.fn();
const revokeUrl = vi.fn();
let handlers: Array<[string, EventListenerOrEventListenerObject]>;

function deferred() {
  let resolve!: (value: Blob | null) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Blob | null>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function start(custom = true) {
  localStorage.setItem("star-isle:background-settings", JSON.stringify({ custom, scrimOpacity: 0.45, blur: 0 }));
  const listenerSpy = vi.spyOn(window, "addEventListener");
  new Function("require", clientCode)((name: string) => {
    if (name === "./public-background-storage") return { loadPublicBackgroundBlob: loadBlob };
    if (name === "@/lib/public-ui") return { parsePublicBackgroundSettings };
    throw new Error("Unexpected background script import");
  });
  handlers = listenerSpy.mock.calls.flatMap(([name, handler]) => handler ? [[name, handler] as [string, EventListenerOrEventListenerObject]] : []);
}

function change(settings: Partial<PublicBackgroundSettings>, blob?: Blob) {
  window.dispatchEvent(new CustomEvent("star-isle:background-change", {
    detail: { settings: { url: null, custom: false, scrimOpacity: 0.45, blur: 0, ...settings }, blob },
  }));
}

function image() {
  return document.querySelector<HTMLElement>("[data-public-background-image]")!;
}

beforeEach(() => {
  handlers = [];
  vi.resetAllMocks();
  let urlCount = 0;
  createUrl.mockImplementation(() => `blob:background-${++urlCount}`);
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = createUrl;
    static revokeObjectURL = revokeUrl;
  });
  document.body.innerHTML = '<div data-public-background-layer hidden><div data-public-background-image></div><div data-public-background-scrim></div></div>';
});

afterEach(() => {
  for (const [name, handler] of handlers) window.removeEventListener(name, handler);
  document.body.innerHTML = "";
  localStorage.removeItem("star-isle:background-settings");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("public background asynchronous updates", () => {
  it("does not overwrite a new preset when an old custom restore completes", async () => {
    const pending = deferred();
    loadBlob.mockReturnValueOnce(pending.promise);
    start();
    change({ url: "/presets/new.svg" });
    pending.resolve(new Blob(["old"]));
    await Promise.resolve();

    expect(image().style.backgroundImage).toContain("/presets/new.svg");
    expect(createUrl).not.toHaveBeenCalled();
  });

  it("only applies the latest custom restore even if the older read resolves first", async () => {
    const oldRead = deferred();
    const newRead = deferred();
    loadBlob.mockReturnValueOnce(oldRead.promise).mockReturnValueOnce(newRead.promise);
    start();
    change({ custom: true, scrimOpacity: 0.2 });
    oldRead.resolve(new Blob(["old"]));
    await Promise.resolve();
    expect(createUrl).not.toHaveBeenCalled();
    newRead.resolve(new Blob(["new"]));
    await Promise.resolve();

    expect(createUrl).toHaveBeenCalledOnce();
    expect(image().style.backgroundImage).toContain("blob:background-1");
    expect(document.querySelector<HTMLElement>("[data-public-background-scrim]")!.style.opacity).toBe("0.2");
  });

  it("does not hide a newly selected preset when an outdated restore rejects", async () => {
    const pending = deferred();
    loadBlob.mockReturnValueOnce(pending.promise);
    start();
    change({ url: "/presets/new.svg" });
    pending.reject(new Error("synthetic storage failure"));
    await Promise.resolve();

    expect(image().style.backgroundImage).toContain("/presets/new.svg");
    expect(document.querySelector<HTMLElement>("[data-public-background-layer]")!.hidden).toBe(false);
  });

  it("revokes replaced URLs and prevents a pending restore from recreating one after pagehide", async () => {
    const pending = deferred();
    loadBlob.mockReturnValueOnce(pending.promise);
    start(false);
    change({ custom: true }, new Blob(["first"]));
    change({ custom: true }, new Blob(["second"]));
    expect(revokeUrl).toHaveBeenCalledWith("blob:background-1");
    change({ custom: true });
    window.dispatchEvent(new Event("pagehide"));
    expect(revokeUrl).toHaveBeenCalledWith("blob:background-2");
    pending.resolve(new Blob(["late"]));
    await Promise.resolve();

    expect(createUrl).toHaveBeenCalledTimes(2);
  });
});
