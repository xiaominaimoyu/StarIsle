// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./WaxSealSvg", () => ({ default: () => null }));

import { createElement as h } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import NarrativeEntry from "./NarrativeEntry";

const reactTestGlobals = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean };

describe("NarrativeEntry dual entrance events", () => {
  let root: Root;
  let container: HTMLDivElement;
  let publicHandler: ReturnType<typeof vi.fn>;
  let privateHandler: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    reactTestGlobals.IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    publicHandler = vi.fn();
    privateHandler = vi.fn();
    window.addEventListener("narrative:public-enter", publicHandler);
    window.addEventListener("narrative:private-enter", privateHandler);
  });

  afterEach(() => {
    window.removeEventListener("narrative:public-enter", publicHandler);
    window.removeEventListener("narrative:private-enter", privateHandler);
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
    delete reactTestGlobals.IS_REACT_ACT_ENVIRONMENT;
  });

  it("dispatches the public event after the seal melt completes", async () => {
    await act(async () => {
      root.render(h(NarrativeEntry));
    });

    const seal = container.querySelector<HTMLAnchorElement>('a[aria-label="融化封蜡，进入公开页"]');
    expect(seal?.getAttribute("href")).toBe("/");

    await act(async () => {
      seal?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });

    expect(publicHandler).toHaveBeenCalledTimes(1);
    expect(privateHandler).not.toHaveBeenCalled();
    const event = publicHandler.mock.calls[0][0] as CustomEvent;
    expect(event.detail.method).toBe("seal");
  });

  it("keeps a keyboard fallback for the private tear path", async () => {
    await act(async () => {
      root.render(h(NarrativeEntry));
    });

    const tearZone = container.querySelector<HTMLAnchorElement>('a[aria-label="撕开信封，进入私人登录页"]');
    expect(tearZone?.getAttribute("href")).toBe("/login");

    await act(async () => {
      tearZone?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });

    expect(privateHandler).toHaveBeenCalledTimes(1);
    expect(publicHandler).not.toHaveBeenCalled();
    const event = privateHandler.mock.calls[0][0] as CustomEvent;
    expect(event.detail.method).toBe("tear");
  });

  it("mounts the paper fleck layer inside the tear stage only", async () => {
    await act(async () => {
      root.render(h(NarrativeEntry));
    });

    const canvas = container.querySelector("canvas.narrative-tear-debris");
    expect(canvas).not.toBeNull();
    // The flecks ride the envelope layer so the tear geometry stays in sync.
    expect(canvas?.parentElement?.className).toContain("narrative-entry__envelope");
    expect(canvas?.closest(".narrative-entry__stage")).not.toBeNull();
    expect(canvas?.getAttribute("aria-hidden")).toBe("true");
  });

  it("drops the fleck layer entirely under reduced motion", async () => {
    const matchMedia = vi.spyOn(window, "matchMedia").mockImplementation(
      () =>
        ({
          matches: true,
          media: "(prefers-reduced-motion: reduce)",
          addEventListener: () => {},
          removeEventListener: () => {},
        }) as unknown as MediaQueryList,
    );

    await act(async () => {
      root.render(h(NarrativeEntry));
    });

    expect(container.querySelector("canvas.narrative-tear-debris")).toBeNull();
    expect(container.querySelector(".narrative-entry")?.getAttribute("data-quality")).toBe("reduced");
    matchMedia.mockRestore();
  });
});
