import { beforeEach, describe, expect, it, vi } from "vitest";

const clientMocks = vi.hoisted(() => ({
  ApiError: class ApiError extends Error {
    code: string;
    status: number;
    requestId: string | null;

    constructor(code: string, message: string, status: number, requestId: string | null) {
      super(message);
      this.name = "ApiError";
      this.code = code;
      this.status = status;
      this.requestId = requestId;
    }
  },
}));

const reactHarness = vi.hoisted(() => ({
  effect: null as null | (() => void | (() => void)),
  stateSetters: [] as Array<ReturnType<typeof vi.fn>>,
  stateValues: [] as unknown[],
}));

const apiMocks = vi.hoisted(() => ({
  fetchCsrf: vi.fn(),
  fetchMe: vi.fn(),
  refresh: vi.fn(),
}));

const sessionMocks = vi.hoisted(() => ({
  getCsrf: vi.fn(),
  setAnonymous: vi.fn(),
  setAuthenticated: vi.fn(),
  setCsrf: vi.fn(),
}));

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useEffect: vi.fn((effect: () => void | (() => void)) => {
      reactHarness.effect = effect;
    }),
    useState: vi.fn((initial: unknown) => {
      const value = reactHarness.stateValues.length > 0
        ? reactHarness.stateValues.shift()
        : initial;
      const setter = vi.fn();
      reactHarness.stateSetters.push(setter);
      return [value, setter];
    }),
  };
});

vi.mock("@/lib/api/modules", () => apiMocks);
vi.mock("@/lib/api/client", () => clientMocks);
vi.mock("@/stores/session", () => sessionMocks);

import SessionGate from "./SessionGate";

interface TestElement {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown };
}

function findHostElement(node: unknown, type: string): TestElement | null {
  if (!node || typeof node !== "object") return null;
  const element = node as TestElement;
  if (element.type === type) return element;
  const children = element.props?.children;
  for (const child of Array.isArray(children) ? children : [children]) {
    const match = findHostElement(child, type);
    if (match) return match;
  }
  return null;
}

function renderGate(status?: string) {
  reactHarness.stateValues = status ? [status, 0] : [];
  return SessionGate({ children: "受保护内容" });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  reactHarness.effect = null;
  reactHarness.stateSetters = [];
  reactHarness.stateValues = [];
  sessionMocks.getCsrf.mockReturnValue("csrf-token");
  apiMocks.fetchCsrf.mockResolvedValue({ csrfToken: "csrf-token", expiresAt: "2099-01-01T00:00:00Z" });
});

describe("SessionGate failure classification", () => {
  it.each([0, 503])("shows a retryable gate error for transient status %s without redirecting", async (status) => {
    apiMocks.fetchMe.mockRejectedValue(new clientMocks.ApiError("INTERNAL", "暂时不可用", status, "request-1"));
    renderGate();

    const cleanup = reactHarness.effect?.();
    await vi.waitFor(() => expect(reactHarness.stateSetters[0]).toHaveBeenCalledWith("error"));

    expect(sessionMocks.setAnonymous).not.toHaveBeenCalled();
    cleanup?.();
  });

  it("keeps an expired session on the existing login redirect path", async () => {
    const testWindow = {
      location: { pathname: "/console/kb", search: "?view=recent", href: "" },
    };
    vi.stubGlobal("window", testWindow);
    apiMocks.fetchMe.mockRejectedValue(new clientMocks.ApiError("AUTH_REQUIRED", "需要登录", 401, "request-me"));
    apiMocks.refresh.mockRejectedValue(new clientMocks.ApiError("AUTH_REQUIRED", "刷新失败", 401, "request-refresh"));
    renderGate();

    const cleanup = reactHarness.effect?.();
    await vi.waitFor(() => expect(sessionMocks.setAnonymous).toHaveBeenCalledOnce());

    expect(testWindow.location.href).toBe("/login?redirect=%2Fconsole%2Fkb%3Fview%3Drecent");
    cleanup?.();
  });

  it("shows the retryable error when refresh reaches a temporary server failure", async () => {
    const testWindow = {
      location: { pathname: "/console/kb", search: "", href: "" },
    };
    vi.stubGlobal("window", testWindow);
    apiMocks.fetchMe.mockRejectedValue(new clientMocks.ApiError("AUTH_REQUIRED", "需要登录", 401, "request-me"));
    apiMocks.refresh.mockRejectedValue(new clientMocks.ApiError("INTERNAL", "暂时不可用", 503, "request-refresh"));
    renderGate();

    const cleanup = reactHarness.effect?.();
    await vi.waitFor(() => expect(reactHarness.stateSetters[0]).toHaveBeenCalledWith("error"));

    expect(sessionMocks.setAnonymous).not.toHaveBeenCalled();
    expect(testWindow.location.href).toBe("");
    cleanup?.();
  });

  it("keeps a refresh CSRF failure retryable instead of treating the session as anonymous", async () => {
    const testWindow = {
      location: { pathname: "/console/kb", search: "", href: "" },
    };
    vi.stubGlobal("window", testWindow);
    apiMocks.fetchMe.mockRejectedValue(new clientMocks.ApiError("AUTH_REQUIRED", "需要登录", 401, "request-me"));
    apiMocks.refresh.mockRejectedValue(new clientMocks.ApiError("CSRF_REQUIRED", "CSRF 校验失败", 403, "request-refresh"));
    renderGate();

    const cleanup = reactHarness.effect?.();
    await vi.waitFor(() => expect(reactHarness.stateSetters[0]).toHaveBeenCalledWith("error"));

    expect(sessionMocks.setAnonymous).not.toHaveBeenCalled();
    expect(testWindow.location.href).toBe("");
    cleanup?.();
  });

  it("keeps the existing inline forbidden result for an authenticated 403", async () => {
    apiMocks.fetchMe.mockRejectedValue(new clientMocks.ApiError("FORBIDDEN", "无权访问", 403, "request-403"));
    renderGate();

    const cleanup = reactHarness.effect?.();
    await vi.waitFor(() => expect(reactHarness.stateSetters[0]).toHaveBeenCalledWith("forbidden"));

    expect(sessionMocks.setAnonymous).not.toHaveBeenCalled();
    cleanup?.();
  });

  it("renders a retry control that starts another session check", () => {
    const tree = renderGate("error");
    const button = findHostElement(tree, "button");

    expect(button?.props?.children).toBe("重试");
    expect(button?.props?.onClick).toBeTypeOf("function");
    (button?.props?.onClick as () => void)();

    const retryUpdate = reactHarness.stateSetters[1].mock.calls[0]?.[0] as (current: number) => number;
    expect(retryUpdate(2)).toBe(3);
  });

  it("offers a working login action from a forbidden view and preserves its destination", () => {
    const testWindow = { location: { pathname: "/console/files/", search: "?page=2", href: "" } };
    vi.stubGlobal("window", testWindow);
    const tree = renderGate("forbidden");
    const button = findHostElement(tree, "button");
    expect(button?.props?.children).toBe("重新登录");
    (button?.props?.onClick as () => void)();
    expect(sessionMocks.setAnonymous).toHaveBeenCalledOnce();
    expect(sessionMocks.setCsrf).toHaveBeenCalledWith(null, null);
    expect(testWindow.location.href).toBe("/login?redirect=%2Fconsole%2Ffiles%2F%3Fpage%3D2");
  });
});
