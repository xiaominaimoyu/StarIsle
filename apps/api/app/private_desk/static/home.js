import { PRIVATE_NAV } from "./private-state.mjs";
import { bindLogout, ensureSession, renderNav } from "./desk.js";

if (await ensureSession()) {
  renderNav("home");
  bindLogout();
  const list = document.querySelector("[data-rooms]");
  for (const item of PRIVATE_NAV) {
    const row = document.createElement("li");
    row.className = "panel";
    const title = document.createElement("strong");
    title.textContent = item.label;
    row.append(title);
    const note = document.createElement("p");
    note.className = "quiet";
    if (item.enabled && item.href) {
      const link = document.createElement("a");
      link.href = item.href;
      const openLabel = { album: "打开相册", messages: "打开来信", notes: "打开手记" };
      link.textContent = openLabel[item.id] || "打开";
      note.append(link);
    } else {
      note.textContent = "稍后开放";
    }
    row.append(note);
    list.append(row);
  }
}
