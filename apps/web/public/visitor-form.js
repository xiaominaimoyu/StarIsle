(() => {
  const form = document.querySelector("#visitor-form");
  if (!form) return;

  const status = document.querySelector("#visitor-status");
  const verification = document.querySelector("#visitor-verification");
  const retry = document.querySelector("#visitor-verification-retry");
  const widget = document.querySelector("#visitor-turnstile");
  const apiScript = document.querySelector("#visitor-turnstile-api");
  const count = document.querySelector("#visitor-body-count");
  const submit = form.querySelector("button[type=submit]");
  const fields = ["name", "contact", "body"].map((name) => form.elements.namedItem(name));
  let widgetId;
  let verified = false;
  let sending = false;

  function updateControls() {
    submit.disabled = sending || !verified;
    submit.textContent = sending ? "正在送出…" : "留下这句话";
    retry.disabled = sending;
    form.setAttribute("aria-busy", String(sending));
    fields.forEach((field) => { field.readOnly = sending; });
  }

  function updateCount() {
    count.textContent = fields[2].value.length + " / 2000";
  }

  function show(message, ok = false) {
    status.textContent = message;
    status.className = ok ? "visitor-status visitor-status--ok" : "visitor-status";
  }

  function verificationFailed(message) {
    verified = false;
    verification.textContent = message;
    retry.hidden = widgetId === undefined;
    updateControls();
  }

  function resetVerification() {
    verified = false;
    retry.hidden = true;
    verification.textContent = "请完成新的人机验证后再发送。";
    updateControls();
    try {
      window.turnstile.reset(widgetId);
    } catch {
      verificationFailed("验证暂时不可用，请重试验证，或通过下方邮箱联系。");
    }
  }

  function initializeVerification() {
    if (widgetId !== undefined || !window.turnstile) return;
    clearTimeout(loadTimeout);
    try {
      widgetId = window.turnstile.render(widget, {
        sitekey: widget.dataset.sitekey,
        size: "compact",
        theme: "auto",
        callback() {
          verified = true;
          verification.textContent = "验证通过，可以留下这句话。";
          retry.hidden = true;
          updateControls();
        },
        "expired-callback"() {
          verificationFailed("验证已过期，请重新验证。留言仍在这页上。");
        },
        "error-callback"() {
          verificationFailed("验证未完成，请重试验证，或通过下方邮箱联系。");
        },
        "timeout-callback"() {
          verificationFailed("验证等待超时，请重新验证。留言仍在这页上。");
        },
      });
    } catch {
      verificationFailed("验证未能加载，请刷新页面，或通过下方邮箱联系。");
    }
  }

  fields[2].addEventListener("input", updateCount);
  retry.addEventListener("click", () => {
    if (!sending) resetVerification();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (sending || !form.reportValidity()) return;
    let token = "";
    try {
      if (verified) token = window.turnstile.getResponse(widgetId);
    } catch {
      // A failed/expired widget must never submit an old response.
    }
    if (!token) {
      verificationFailed("请先完成人机验证。留言仍在这页上。");
      return;
    }

    const payload = {
      name: fields[0].value,
      contact: fields[1].value,
      body: fields[2].value,
      turnstileToken: token,
    };
    sending = true;
    updateControls();
    show("正在送出，请稍候…");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch("/api/visitor-messages", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (response.status === 201) {
        form.reset();
        updateCount();
        show("已经留下。谢谢你的来信，主人取信后会看到。", true);
      } else if (response.status === 429) {
        show("留言有些频繁，请稍后再试。内容仍在这页上。");
      } else if (response.status === 403) {
        show("人机验证没有通过，请重新验证后再发送。内容已保留。");
      } else if (response.status >= 500) {
        show("留言窗口暂时不可用，内容已保留。请稍后再试，或通过下方邮箱联系。");
      } else {
        show("这次没有送出，请检查内容后重新验证。内容已保留。");
      }
    } catch {
      show(controller.signal.aborted
        ? "等待超时，暂时无法确认是否送达。内容已保留，请稍后确认再重试，以免重复留言。"
        : "连接中断，暂时无法确认是否送达。内容已保留，请稍后确认再重试，以免重复留言。");
    } finally {
      clearTimeout(timeout);
      // Siteverify may consume a token even when the request does not succeed.
      sending = false;
      resetVerification();
    }
  });

  updateControls();
  updateCount();
  const loadTimeout = setTimeout(() => {
    verificationFailed("验证加载较慢，请刷新页面，或通过下方邮箱联系。");
  }, 15000);
  apiScript?.addEventListener("load", initializeVerification, { once: true });
  apiScript?.addEventListener("error", () => {
    clearTimeout(loadTimeout);
    verificationFailed("验证未能加载，请刷新页面，或通过下方邮箱联系。");
  }, { once: true });
  initializeVerification();
})();
