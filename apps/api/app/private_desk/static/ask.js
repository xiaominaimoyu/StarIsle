import { bindLogout, csrfToken, ensureSession, renderNav } from "./desk.js";

const embedLine = document.querySelector("[data-embed]");
const chatLine = document.querySelector("[data-chat]");
const indexLine = document.querySelector("[data-index]");
const answer = document.querySelector("[data-answer]");
const citations = document.querySelector("[data-citations]");
const query = document.querySelector("[data-query]");

function paintStatus(body) {
  embedLine.textContent = body.embed ? body.embed.message : "嵌入服务没有回应。";
  chatLine.textContent = body.chat ? body.chat.message : "生成服务没有回应。";
  const ready = Number(body.ready || 0);
  const pending = Number(body.pending || 0);
  indexLine.textContent = `已索引 ${ready} 篇，待继续 ${pending} 篇。`;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = await csrfToken();
  if (options.body) headers["Content-Type"] = "application/json";
  return fetch(path, { credentials: "same-origin", ...options, headers });
}

async function refreshStatus() {
  const response = await api("/api/v1/desk-ask/status");
  if (response.status === 401) {
    window.location.assign("/private/login?next=/private/ask");
    return null;
  }
  if (!response.ok) return null;
  const body = await response.json();
  paintStatus(body);
  return body;
}

async function runIndex(mode) {
  const response = await api("/api/v1/desk-ask/index", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
  if (!response.ok) {
    indexLine.textContent = "索引没有完成。";
    return;
  }
  paintStatus(await response.json());
}

function addCitation(citation) {
  const link = document.createElement("a");
  link.className = "letter-row";
  link.href = citation.href || "/private/ask";
  const title = document.createElement("strong");
  title.textContent = citation.title || "原文";
  const snippet = document.createElement("span");
  snippet.textContent = citation.snippet || "";
  link.append(title, snippet);
  citations.append(link);
}

async function ask(event) {
  event.preventDefault();
  answer.textContent = "";
  citations.replaceChildren();
  const response = await api("/api/v1/desk-ask/query", {
    method: "POST",
    body: JSON.stringify({ query: query.value }),
  });
  if (!response.ok || !response.body) {
    answer.textContent = "提问没有送出去。";
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((item) => item.startsWith("data:"));
      if (!line) continue;
      let eventBody;
      try {
        eventBody = JSON.parse(line.slice(5).trim());
      } catch {
        continue;
      }
      if (eventBody.event === "token") answer.textContent += eventBody.token || "";
      if (eventBody.event === "citation" && eventBody.citation) addCitation(eventBody.citation);
      if (eventBody.event === "error") answer.textContent = eventBody.message || "提问没有完成。";
    }
  }
}

if (await ensureSession()) {
  renderNav("ask");
  bindLogout();
  const status = await refreshStatus();
  if (status && status.embed && status.embed.state === "up" && status.pending > 0) {
    await runIndex("continue");
  }
  document.querySelector("[data-continue]").addEventListener("click", () => runIndex("continue"));
  document.querySelector("[data-rebuild]").addEventListener("click", () => runIndex("rebuild"));
  document.querySelector("#ask-form").addEventListener("submit", ask);
}
