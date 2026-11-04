// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { URL as FileURL } from "node:url";
import { transformSync } from "esbuild";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const component = readFileSync(new FileURL("./PublicSiteNav.astro", import.meta.url), "utf8");
const script = component.match(/<script>([\s\S]*?)<\/script>/)?.[1];
if (!script) throw new Error("PublicSiteNav client script is missing");
const clientCode = transformSync(script, { loader: "ts" }).code;

let desktop = false;
let mediaChange: (() => void) | undefined;
let keyHandler: EventListenerOrEventListenerObject | undefined;

beforeEach(() => {
  desktop = false;
  mediaChange = undefined;
  keyHandler = undefined;
  vi.spyOn(window, "matchMedia").mockImplementation(() => ({
    get matches() { return desktop; },
    addEventListener: (_name: string, callback: () => void) => { mediaChange = callback; },
  }) as unknown as MediaQueryList);
  document.body.innerHTML = `
    <nav data-public-desktop-nav aria-label="主导航">
      <a id="desktop-home" href="/">首页</a>
      <a id="desktop-current" href="/projects" aria-current="page">项目</a>
    </nav>
    <button data-public-nav-trigger aria-expanded="false">打开导航</button>
    <div data-public-modal data-public-nav-dialog hidden>
      <div data-public-nav-panel tabindex="-1">
        <button data-public-nav-close>关闭</button>
        <a href="/projects">项目</a>
      </div>
    </div>`;
  const listenerSpy = vi.spyOn(document, "addEventListener");
  new Function(clientCode)();
  keyHandler = listenerSpy.mock.calls.find(([type]) => type === "keydown")?.[1] ?? undefined;
});

afterEach(() => {
  if (keyHandler) document.removeEventListener("keydown", keyHandler);
  document.body.innerHTML = "";
  document.body.className = "";
  vi.restoreAllMocks();
});

function openMenu(): HTMLButtonElement {
  const trigger = document.querySelector<HTMLButtonElement>("[data-public-nav-trigger]")!;
  trigger.focus();
  trigger.click();
  return trigger;
}

function changeToDesktop(): void {
  desktop = true;
  mediaChange?.();
}

describe("public navigation breakpoint transitions", () => {
  it("closes an open mobile menu, releases scrolling and moves focus to the desktop current page", () => {
    const trigger = openMenu();
    expect(document.body.classList.contains("public-modal-open")).toBe(true);
    changeToDesktop();

    expect(document.querySelector<HTMLElement>("[data-public-nav-dialog]")!.hidden).toBe(true);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(document.body.classList.contains("public-modal-open")).toBe(false);
    expect(document.activeElement?.id).toBe("desktop-current");
  });

  it("preserves another open dialog's scroll lock and can reopen the menu when mobile again", () => {
    openMenu();
    const otherModal = document.createElement("div");
    otherModal.setAttribute("data-public-modal", "");
    document.body.append(otherModal);
    changeToDesktop();

    expect(document.querySelector<HTMLElement>("[data-public-nav-dialog]")!.hidden).toBe(true);
    expect(document.body.classList.contains("public-modal-open")).toBe(true);
    otherModal.remove();
    desktop = false;
    mediaChange?.();
    const trigger = openMenu();
    document.querySelector<HTMLButtonElement>("[data-public-nav-close]")!.click();
    expect(document.body.classList.contains("public-modal-open")).toBe(false);
    expect(document.activeElement).toBe(trigger);
  });

  it("does not open a hidden mobile dialog on desktop", () => {
    changeToDesktop();
    openMenu();
    expect(document.querySelector<HTMLElement>("[data-public-nav-dialog]")!.hidden).toBe(true);
    expect(document.body.classList.contains("public-modal-open")).toBe(false);
  });
});
