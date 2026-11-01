// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { URL as FileURL } from "node:url";
import { transformSync } from "esbuild";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const component = readFileSync(new FileURL("./PublicRepoGrid.astro", import.meta.url), "utf8");
const script = component.match(/<script>([\s\S]*?)<\/script>/)?.[1];
if (!script) throw new Error("PublicRepoGrid client script is missing");
const clientCode = transformSync(script, { loader: "ts" }).code;

function visibleCards(): string[] {
  return Array.from(document.querySelectorAll<HTMLElement>("[data-repo-card]"))
    .filter((card) => !card.hidden)
    .map((card) => card.id);
}

function sourceButton(source: string): HTMLButtonElement {
  return document.querySelector<HTMLButtonElement>(`button[data-repo-source="${source}"]`)!;
}

describe("public repository controls", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <section data-public-repo-grid>
        <div role="group" aria-label="Repository source">
          <button data-repo-source="all">All</button>
          <button data-repo-source="github">GitHub</button>
          <button data-repo-source="gitee">Gitee</button>
        </div>
        <div role="group" aria-label="Repository order">
          <button data-repo-sort="featured">Featured</button>
          <button data-repo-sort="updated">Updated</button>
        </div>
        <p data-repo-empty hidden>No repositories</p>
        <ul data-repo-list>
          <li id="older-featured" data-repo-card data-repo-source="github"
              data-repo-featured="true" data-repo-updated-at="2026-01-01">
            <a href="https://github.com/example/repository">GitHub repository</a>
          </li>
          <li id="newer" data-repo-card data-repo-source="gitee"
              data-repo-featured="false" data-repo-updated-at="2026-02-01">
            <a href="https://gitee.com/example/repository">Gitee repository</a>
          </li>
        </ul>
      </section>`;
    new Function(clientCode)();
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("filters and sorts while keeping pressed state on controls only", () => {
    expect(visibleCards()).toEqual(["older-featured", "newer"]);
    expect(sourceButton("all").getAttribute("aria-pressed")).toBe("true");
    sourceButton("github").click();
    expect(visibleCards()).toEqual(["older-featured"]);
    expect(sourceButton("github").getAttribute("aria-pressed")).toBe("true");
    expect(sourceButton("all").getAttribute("aria-pressed")).toBe("false");

    sourceButton("all").click();
    document.querySelector<HTMLButtonElement>('button[data-repo-sort="updated"]')!.click();
    expect(visibleCards()).toEqual(["newer", "older-featured"]);
    for (const card of document.querySelectorAll("[data-repo-card]")) {
      expect(card.hasAttribute("aria-pressed")).toBe(false);
      expect(card.className).toBe("");
    }
  });

  it("does not change the selected source when a repository link is clicked", () => {
    const link = document.querySelector<HTMLAnchorElement>("#older-featured a")!;
    link.addEventListener("click", (event) => event.preventDefault());
    link.click();

    expect(sourceButton("all").getAttribute("aria-pressed")).toBe("true");
    expect(visibleCards()).toEqual(["older-featured", "newer"]);
  });
});
