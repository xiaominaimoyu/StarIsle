import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

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

const apiMocks = vi.hoisted(() => ({ fetchCsrf: vi.fn(), login: vi.fn() }));
const sessionMocks = vi.hoisted(() => ({
  getCsrf: vi.fn(),
  setAuthenticated: vi.fn(),
  setCsrf: vi.fn(),
}));

vi.mock("@/lib/api/modules", () => apiMocks);
vi.mock("@/lib/api/client", () => clientMocks);
vi.mock("@/stores/session", () => sessionMocks);
vi.mock("@/components/ui/Button", () => ({ default: vi.fn(() => null) }));
vi.mock("./FloatInput", () => ({ default: vi.fn(() => null) }));

import LoginForm, { classifyLoginFailure, loginWithCsrf, resolveLoginRedirect } from "./LoginForm";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("LoginForm redirect policy", () => {
  const origin = "https://starisle.example";

  it("keeps an internal path with its query and fragment", () => {
    const target = "/console/kb?view=recent#answer";
    expect(resolveLoginRedirect(`?redirect=${encodeURIComponent(target)}`, origin)).toBe(target);
  });

  it.each([
    "https://evil.example/",
    "//evil.example/",
    "/\\evil.example/",
    "javascript:alert(1)",
    "console",
  ])("falls back for a non-internal redirect: %s", (target) => {
    expect(resolveLoginRedirect(`?redirect=${encodeURIComponent(target)}`, origin)).toBe("/console");
  });

  it("uses the console default when the parameter is absent or invalid", () => {
    expect(resolveLoginRedirect("", origin)).toBe("/console");
    expect(resolveLoginRedirect("?redirect=%", origin)).toBe("/console");
  });

  it("can render on the server without reading browser globals", () => {
    expect(() => renderToStaticMarkup(createElement(LoginForm))).not.toThrow();
  });
});

describe("LoginForm failure mapping", () => {
  it("keeps authentication failures generic", () => {
    expect(classifyLoginFailure(new clientMocks.ApiError("AUTH_REQUIRED", "backend detail", 401, null))).toEqual({
      code: "401",
      message: "无法登录,请检查密码后重试。",
    });
  });

  it("starts the existing cooldown for rate limiting", () => {
    expect(classifyLoginFailure(new clientMocks.ApiError("RATE_LIMITED", "slow down", 429, null))).toEqual({
      code: "429",
      message: "尝试过于频繁,请稍候再试。",
      cooldownSeconds: 30,
    });
  });

  it.each([
    new clientMocks.ApiError("CSRF_REQUIRED", "csrf", 403, null),
    new clientMocks.ApiError("INTERNAL", "offline", 0, null),
  ])("maps CSRF and transport failures to a retryable security error", (error) => {
    expect(classifyLoginFailure(error)).toEqual({
      code: "csrf",
      message: "安全验证获取失败,请重试。",
    });
  });

  it("preserves a normalized API message and hides unknown error details", () => {
    expect(classifyLoginFailure(new clientMocks.ApiError("INTERNAL", "服务暂时不可用", 503, "request-1"))).toEqual({
      code: "503",
      message: "服务暂时不可用",
    });
    expect(classifyLoginFailure(new Error("secret detail"))).toEqual({
      code: "unknown",
      message: "网络请求失败,请检查连接后重试。",
    });
  });
});

describe("LoginForm CSRF recovery", () => {
  it("discards a server-rejected token so the next submit performs a fresh handshake", async () => {
    const csrfFailure = new clientMocks.ApiError("CSRF_REQUIRED", "csrf", 403, null);
    const user = {
      id: "admin",
      role: "admin",
      sessionExpiresAt: "2099-01-01T00:00:00Z",
    };
    sessionMocks.getCsrf.mockReturnValueOnce("stale-token").mockReturnValueOnce(null);
    apiMocks.login.mockRejectedValueOnce(csrfFailure).mockResolvedValueOnce(user);
    apiMocks.fetchCsrf.mockResolvedValue({
      csrfToken: "fresh-token",
      expiresAt: "2099-01-01T00:00:00Z",
    });

    await expect(loginWithCsrf("secret")).rejects.toBe(csrfFailure);
    expect(sessionMocks.setCsrf).toHaveBeenCalledWith(null, null);
    expect(apiMocks.fetchCsrf).not.toHaveBeenCalled();

    await expect(loginWithCsrf("secret")).resolves.toBe(user);
    expect(apiMocks.fetchCsrf).toHaveBeenCalledOnce();
    expect(sessionMocks.setCsrf).toHaveBeenCalledWith("fresh-token", Date.parse("2099-01-01T00:00:00Z"));
    expect(apiMocks.login.mock.calls).toEqual([
      [{ password: "secret" }, "stale-token"],
      [{ password: "secret" }, "fresh-token"],
    ]);
  });
});
