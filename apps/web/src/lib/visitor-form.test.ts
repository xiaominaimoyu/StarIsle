// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { URL as FileURL } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const clientCode = readFileSync(new FileURL("../../public/visitor-form.js", import.meta.url), "utf8");
type WidgetOptions = {
  callback: (token: string) => void;
  "expired-callback": () => void;
  "error-callback": (code: string) => void;
  "timeout-callback": () => void;
};

let widgetOptions: WidgetOptions | undefined;
let token: string;
const widgetId = "visitor-widget-7";
const fetchMock = vi.fn<typeof fetch>();
const render = vi.fn((_container: HTMLElement, options: WidgetOptions) => {
  widgetOptions = options;
  return widgetId;
});
const getResponse = vi.fn(() => token);
const reset = vi.fn(() => { token = ""; });
const turnstile = { render, getResponse, reset };

function form() { return document.querySelector<HTMLFormElement>("#visitor-form")!; }
function submitButton() { return form().querySelector<HTMLButtonElement>("button[type=submit]")!; }
function field(name: string) { return form().elements.namedItem(name) as HTMLInputElement | HTMLTextAreaElement; }
function status() { return document.querySelector<HTMLElement>("#visitor-status")!; }
function completeVerification(value = "synthetic-token-first") {
  token = value;
  widgetOptions?.callback(value);
}
function submit() {
  const event = new Event("submit", { bubbles: true, cancelable: true });
  form().dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
}
function fill() {
  field("name").value = "访客";
  field("contact").value = "visitor@example.test";
  field("body").value = "留在这座岛上的一段测试文字。";
  field("body").dispatchEvent(new Event("input", { bubbles: true }));
}
async function settle() {
  for (let index = 0; index < 5; index += 1) await Promise.resolve();
}
function start() { new Function(clientCode)(); }

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock.mockReset();
  widgetOptions = undefined;
  token = "";
  document.body.innerHTML = `
    <form id="visitor-form" method="post" action="/api/visitor-messages">
      <input id="visitor-name" name="name" required maxlength="80">
      <input id="visitor-contact" name="contact" required minlength="3" maxlength="120">
      <textarea id="visitor-body" name="body" required maxlength="2000"></textarea>
      <span id="visitor-body-count">0 / 2000</span>
      <div id="visitor-turnstile" data-sitekey="synthetic-site-key"></div>
      <p id="visitor-verification" role="status">正在准备人机验证…</p>
      <button type="button" id="visitor-verification-retry" hidden>重试验证</button>
      <p id="visitor-status" role="status" aria-live="polite"></p>
      <button type="submit" disabled>留下这句话</button>
    </form>
    <script id="visitor-turnstile-api"></script>`;
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("turnstile", turnstile);
  fetchMock.mockResolvedValue(new Response(null, { status: 201 }));
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("visitor message submission recovery", () => {
  it("waits for verification and renders only the current form's widget", () => {
    start();
    expect(render).toHaveBeenCalledOnce();
    expect(submitButton().disabled).toBe(true);
    completeVerification();
    expect(submitButton().disabled).toBe(false);
    widgetOptions!["expired-callback"]();
    expect(submitButton().disabled).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("preserves a rejected message, resets its widget, and sends a new token only on manual retry", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 403 }));
    start();
    fill();
    completeVerification();
    const body = field("body").value;
    submit();
    await settle();
    expect(field("body").value).toBe(body);
    expect(reset).toHaveBeenCalledWith(widgetId);
    expect(submitButton().disabled).toBe(true);
    expect(status().textContent).toMatch(/验证/);
    expect(fetchMock).toHaveBeenCalledOnce();
    completeVerification("synthetic-token-second");
    expect(fetchMock).toHaveBeenCalledOnce();
    submit();
    await settle();
    const options = fetchMock.mock.calls[1][1]!;
    expect(options.method).toBe("POST");
    expect(JSON.parse(String(options.body))).toMatchObject({ body, turnstileToken: "synthetic-token-second" });
    expect(getResponse).toHaveBeenLastCalledWith(widgetId);
    expect(field("body").value).toBe("");
    expect(status().textContent).toMatch(/已经|送达|留下|成功/);
  });

  it.each([429, 503])("keeps text and unlocks editing after HTTP %s without automatic resend", async (httpStatus) => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: httpStatus }));
    start();
    fill();
    completeVerification();
    submit();
    await settle();
    expect(field("body").value).toContain("测试文字");
    expect(field("body").readOnly).toBe(false);
    expect(form().getAttribute("aria-busy")).not.toBe("true");
    expect(reset).toHaveBeenCalledWith(widgetId);
    expect(submitButton().disabled).toBe(true);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(status().textContent).toMatch(httpStatus === 429 ? /频繁|稍后/ : /服务|暂时/);
  });

  it("locks the submitted fields until completion and ignores a second submit event", async () => {
    let finish!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
    start();
    fill();
    completeVerification();
    submit();
    submit();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(form().getAttribute("aria-busy")).toBe("true");
    expect(submitButton().disabled).toBe(true);
    for (const name of ["name", "contact", "body"]) expect(field(name).readOnly).toBe(true);
    expect(status().textContent).toMatch(/正在|送出中|发送中/);
    finish(new Response(null, { status: 201 }));
    await settle();
    for (const name of ["name", "contact", "body"]) {
      expect(field(name).readOnly).toBe(false);
      expect(field(name).value).toBe("");
    }
    expect(form().getAttribute("aria-busy")).not.toBe("true");
    expect(document.querySelector("#visitor-body-count")?.textContent).toMatch(/^0\s*\/\s*2000$/);
  });

  it("retains input after network failure and makes delivery uncertainty explicit", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("synthetic network loss"));
    start();
    fill();
    completeVerification();
    submit();
    await settle();
    expect(field("contact").value).toBe("visitor@example.test");
    expect(field("body").value).toContain("测试文字");
    expect(field("body").readOnly).toBe(false);
    expect(status().textContent).toMatch(/无法确认|不能确认|尚未确认/);
    expect(reset).toHaveBeenCalledWith(widgetId);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("aborts a stalled request after fifteen seconds and restores editable input", async () => {
    vi.useFakeTimers();
    let requestSignal: AbortSignal | undefined;
    fetchMock.mockImplementationOnce((_input, options) => new Promise<Response>((_resolve, reject) => {
      requestSignal = options?.signal as AbortSignal;
      requestSignal.addEventListener("abort", () => reject(new DOMException("synthetic timeout", "AbortError")), { once: true });
    }));
    start();
    fill();
    completeVerification();
    submit();
    await vi.advanceTimersByTimeAsync(14_999);
    expect(requestSignal?.aborted).toBe(false);
    expect(field("body").readOnly).toBe(true);
    await vi.advanceTimersByTimeAsync(1);
    expect(requestSignal?.aborted).toBe(true);
    expect(field("body").readOnly).toBe(false);
    expect(field("body").value).toContain("测试文字");
    expect(status().textContent).toMatch(/无法确认|不能确认|尚未确认/);
    expect(reset).toHaveBeenCalledWith(widgetId);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("offers a widget retry after verification error and keeps submission disabled until it succeeds", () => {
    start();
    completeVerification();
    widgetOptions!["error-callback"]("synthetic-error");
    expect(submitButton().disabled).toBe(true);
    const retry = document.querySelector<HTMLButtonElement>("#visitor-verification-retry")!;
    expect(retry.hidden).toBe(false);
    retry.click();
    expect(reset).toHaveBeenCalledWith(widgetId);
    expect(submitButton().disabled).toBe(true);
    completeVerification("fresh-token-after-widget-retry");
    expect(submitButton().disabled).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not submit an empty or expired response even after an earlier verification callback", async () => {
    start();
    fill();
    completeVerification();
    token = "";
    submit();
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(field("body").value).toContain("测试文字");
    expect(submitButton().disabled).toBe(true);
  });

  it("waits for a delayed verification script and initializes it once", () => {
    vi.stubGlobal("turnstile", undefined);
    start();
    expect(render).not.toHaveBeenCalled();
    expect(submitButton().disabled).toBe(true);
    vi.stubGlobal("turnstile", turnstile);
    const script = document.querySelector("#visitor-turnstile-api")!;
    script.dispatchEvent(new Event("load"));
    script.dispatchEvent(new Event("load"));
    expect(render).toHaveBeenCalledOnce();
    completeVerification();
    expect(submitButton().disabled).toBe(false);
  });

  it("leaves a blocked verification script safely disabled with an explanation", () => {
    vi.stubGlobal("turnstile", undefined);
    start();
    document.querySelector("#visitor-turnstile-api")!.dispatchEvent(new Event("error"));
    expect(submitButton().disabled).toBe(true);
    expect(document.querySelector("#visitor-verification")?.textContent).toMatch(/失败|加载|暂时|刷新/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does nothing on pages without the message form", () => {
    document.body.innerHTML = "";
    expect(start).not.toThrow();
    expect(render).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
