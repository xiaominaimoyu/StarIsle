const form = document.querySelector("#visitor-form");
const status = document.querySelector("#visitor-status");

function show(message, ok) {
  status.hidden = false;
  status.textContent = message;
  status.className = ok ? "visitor-status visitor-status--ok" : "visitor-status";
}

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = form.querySelector("button[type=submit]");
  submit.disabled = true;
  try {
    const token = window.turnstile ? window.turnstile.getResponse() : "";
    if (!token) {
      show("请先完成人机验证。", false);
      return;
    }
    const response = await fetch("/api/visitor-messages", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: form.elements.name.value,
        contact: form.elements.contact.value,
        body: form.elements.body.value,
        turnstileToken: token,
      }),
    });
    if (response.status === 201) {
      form.reset();
      if (window.turnstile) window.turnstile.reset();
      show("已经留下。主人回来后会在自己的电脑上看到。", true);
      return;
    }
    if (response.status === 429) show("今天写得有点勤，请稍后再来。", false);
    else if (response.status === 403) show("人机验证没有通过，请再试一次。", false);
    else show("这次没有送出，请检查内容后再试。", false);
  } catch {
    show("网络不通，留言还在这页上。", false);
  } finally {
    submit.disabled = false;
  }
});
