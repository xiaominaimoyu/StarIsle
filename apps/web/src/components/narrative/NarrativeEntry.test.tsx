// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../auth/LoginForm", () => ({ default: () => null }));
vi.mock("./WaxSealSvg", () => ({ default: () => null }));

import { createElement as h } from "react";
import { createRoot, type Root } from "react-dom/client";
import { act } from "react";
import NarrativeEntry from "./NarrativeEntry";

describe("NarrativeEntry CustomEvent", () => {
  let root: Root;
  let container: HTMLDivElement;
  let handler: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    handler = vi.fn();
    window.addEventListener("narrative:enter-completed", handler);
  });

  afterEach(() => {
    window.removeEventListener("narrative:enter-completed", handler);
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  it("meltComplete 时派发 narrative:enter-completed 事件", async () => {
    await act(async () => {
      root.render(h(NarrativeEntry));
    });

    const button = container.querySelector("button")!;
    expect(button).toBeTruthy();

    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });

    expect(handler).toHaveBeenCalledTimes(1);
    const event = handler.mock.calls[0][0] as CustomEvent;
    expect(event.type).toBe("narrative:enter-completed");
    expect(typeof event.detail.at).toBe("number");
  });
});
