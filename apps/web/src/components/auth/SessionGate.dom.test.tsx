// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { getCsrf, setAnonymous, setCsrf } from "@/stores/session";

const api = vi.hoisted(() => ({ fetchMe: vi.fn(), fetchCsrf: vi.fn(), refresh: vi.fn() }));
vi.mock("@/lib/api/modules", () => api);
import SessionGate from "./SessionGate";

let root: Root | null;
let container: HTMLDivElement;
const user = { id: "synthetic-admin", role: "admin", sessionExpiresAt: "2099-01-01T00:00:00Z" };
const handshake = { csrfToken: "fresh-csrf", expiresAt: "2099-01-01T00:00:00Z" };

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  setAnonymous();
  setCsrf("stale-csrf", Date.parse(handshake.expiresAt));
  api.fetchCsrf.mockResolvedValue(handshake);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container.remove();
  setAnonymous();
  setCsrf(null, null);
  vi.unstubAllGlobals();
});

async function mount() {
  await act(async () => root!.render(createElement(SessionGate, { children: "受保护内容" })));
}

describe("SessionGate real DOM recovery", () => {
  it("discards a rejected CSRF token and obtains a fresh handshake only when retry is clicked", async () => {
    api.fetchMe.mockRejectedValue(new ApiError("AUTH_REQUIRED", "需要登录", 401, null));
    api.refresh.mockRejectedValueOnce(new ApiError("CSRF_REQUIRED", "校验失败", 403, null))
      .mockResolvedValueOnce(user);
    await mount();

    expect(container.textContent).toContain("会话校验失败");
    expect(getCsrf()).toBeNull();
    expect(api.refresh).toHaveBeenCalledOnce();
    expect(api.fetchCsrf).not.toHaveBeenCalled();

    const retry = container.querySelector("button")!;
    await act(async () => retry.click());

    expect(container.textContent).toBe("受保护内容");
    expect(getCsrf()).toBe("fresh-csrf");
    expect(api.fetchCsrf).toHaveBeenCalledOnce();
    expect(api.refresh.mock.calls).toEqual([["stale-csrf"], ["fresh-csrf"]]);
  });

  it.each(["before-me", "before-refresh"])("does not continue requests after unmount during a handshake %s", async (stage) => {
    let resolveHandshake!: (value: typeof handshake) => void;
    api.fetchCsrf.mockImplementationOnce(() => new Promise((resolve) => { resolveHandshake = resolve; }));
    if (stage === "before-me") {
      setCsrf(null, null);
    } else {
      api.fetchMe.mockImplementationOnce(async () => {
        setCsrf(null, null);
        throw new ApiError("AUTH_REQUIRED", "需要登录", 401, null);
      });
    }
    await mount();
    expect(api.fetchCsrf).toHaveBeenCalledOnce();
    act(() => root!.unmount());
    root = null;
    await act(async () => resolveHandshake(handshake));

    expect(getCsrf()).toBeNull();
    expect(api.fetchMe).toHaveBeenCalledTimes(stage === "before-me" ? 0 : 1);
    expect(api.refresh).not.toHaveBeenCalled();
  });
});
