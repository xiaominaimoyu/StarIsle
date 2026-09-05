import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { getPublicSiteUrl, renderSitemapXml } from "../seo";

const enterPage = readFileSync(new URL("../../pages/enter.astro", import.meta.url), "utf8");
const narrativeComponent = readFileSync(new URL("../../components/narrative/NarrativeEntry.tsx", import.meta.url), "utf8");

describe("/enter static and isolation guards", () => {
  it("uses a noindex, chrome-free shell with real fallback links outside the island", () => {
    expect(enterPage).toContain("noindex");
    expect(enterPage).toContain("chrome={false}");
    expect(enterPage).toContain('href="/"');
    expect(enterPage).toContain('href="/login"');
    expect(enterPage.indexOf('<nav')).toBeLessThan(enterPage.indexOf("<NarrativeEntry"));
  });

  it("keeps the route out of the explicit public sitemap", () => {
    expect(renderSitemapXml(getPublicSiteUrl("https://example.test"), { lastmod: "2026-09-04T00:00:00.000Z" })).not.toContain("/enter");
  });

  it("does not import authentication, session, API, or audio concerns", () => {
    expect(narrativeComponent).not.toMatch(/from\s+["'][^"']*(?:auth|session|api|player|audio)[^"']*["']/i);
  });

  it("listens for reduced-motion changes and cleans up its listener and timer", () => {
    expect(narrativeComponent).toContain('window.matchMedia("(prefers-reduced-motion: reduce)")');
    expect(narrativeComponent).toContain("mediaQuery.removeEventListener");
    expect(narrativeComponent).toContain("window.clearTimeout(completionTimer)");
  });
});
