// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FloatInput from "./FloatInput";
import LoginForm from "./LoginForm";

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

it("keeps the real server-rendered login form unavailable before client handling is ready", () => {
  const html = renderToStaticMarkup(createElement(LoginForm));
  expect(html).toMatch(/<button(?=[^>]*type="submit")(?=[^>]*disabled="")[^>]*>/);
  expect(html).toMatch(/<input(?=[^>]*type="password")(?=[^>]*disabled="")[^>]*>/);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("FloatInput interactions", () => {
  it("actually reveals and hides an explicit password input without losing its value or toggle focus", () => {
    act(() => root.render(createElement(FloatInput, {
      label: "密码", password: true, type: "password", value: "synthetic-password", readOnly: true,
    })));
    const input = container.querySelector("input")!;
    const toggle = container.querySelector("button")!;
    toggle.focus();
    expect(input.type).toBe("password");

    act(() => toggle.click());
    expect(input.type).toBe("text");
    expect(input.value).toBe("synthetic-password");
    expect(toggle.getAttribute("aria-label")).toBe("隐藏密码");
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(document.activeElement).toBe(toggle);

    act(() => toggle.click());
    expect(input.type).toBe("password");
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
  });

  it("keeps caller blur handling and associates both the hint and visible error", () => {
    const onBlur = vi.fn();
    act(() => root.render(createElement(FloatInput, {
      label: "邮箱", type: "email", onBlur, invalid: true, errorText: "请检查邮箱",
      "aria-describedby": "email-hint",
    })));
    const input = container.querySelector("input")!;
    act(() => {
      input.focus();
      input.blur();
    });

    expect(onBlur).toHaveBeenCalledOnce();
    expect(input.type).toBe("email");
    const error = container.querySelector('[role="alert"]');
    expect(error?.textContent).toBe("请检查邮箱");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toEqual(["email-hint", error?.id]);
  });
});
