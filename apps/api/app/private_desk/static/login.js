import { safeNext } from "./private-state.mjs";
import { csrfToken, readJson } from "./desk.js";

const form = document.querySelector("#login-form");
const error = document.querySelector("#login-error");
const password = document.querySelector("#password");

function show(message) {
  error.hidden = false;
  error.textContent = message;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.hidden = true;
  const submit = form.querySelector("button");
  submit.disabled = true;
  try {
    const token = await csrfToken();
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": token,
      },
      body: JSON.stringify({ password: password.value }),
    });
    password.value = "";
    if (response.ok) {
      const next = safeNext(new URLSearchParams(window.location.search).get("next"));
      window.location.assign(next);
      return;
    }
    const body = await readJson(response);
    const code = body && body.error ? body.error.code : "";
    if (response.status === 429 || code === "RATE_LIMITED") show("试得太勤了，请稍后再来。");
    else if (code === "CSRF_REQUIRED") show("页面停太久了，请再试一次。");
    else show("密码不对。这台电脑只有一个主人账号。");
  } catch {
    show("书桌暂时没有应答。请确认它已在本机启动。");
  } finally {
    submit.disabled = false;
  }
});
