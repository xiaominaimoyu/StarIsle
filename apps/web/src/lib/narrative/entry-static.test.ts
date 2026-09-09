import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { getPublicSiteUrl, renderSitemapXml } from "../seo";

const enterPage = readFileSync(new URL("../../pages/enter.astro", import.meta.url), "utf8");
const narrativeComponent = readFileSync(new URL("../../components/narrative/NarrativeEntry.tsx", import.meta.url), "utf8");
const loginForm = readFileSync(new URL("../../components/auth/LoginForm.tsx", import.meta.url), "utf8");
const waxSeal = readFileSync(new URL("../../components/narrative/WaxSealSvg.tsx", import.meta.url), "utf8");
const narrativeStyles = readFileSync(new URL("../../components/narrative/narrative-entry.css", import.meta.url), "utf8");

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

  it("reuses only LoginForm at the authentication boundary", () => {
    expect(narrativeComponent).toContain('import LoginForm from "../auth/LoginForm"');
    expect(narrativeComponent).toContain("<LoginForm />");
    expect(narrativeComponent).not.toMatch(/from\s+["'][^"']*(?:session|api|player|audio)[^"']*["']/i);
    expect(narrativeComponent).not.toContain("fetchCsrf");
    expect(narrativeComponent).not.toContain("setAuthenticated");
    expect(loginForm).toContain("resolveLoginRedirect");
    expect(narrativeComponent).not.toContain("localStorage");
  });

  it("keeps the seal semantic and moves focus to the revealed letter", () => {
    expect(narrativeComponent).toContain('aria-controls="narrative-letter"');
    expect(narrativeComponent).toContain('aria-describedby="narrative-seal-description"');
    expect(narrativeComponent).toContain("aria-expanded={revealed}");
    expect(narrativeComponent).toContain("tabIndex={-1}");
    expect(narrativeComponent).toContain("letterTitleRef.current?.focus()");
    expect(narrativeComponent).toContain('aria-live="polite"');
    expect(narrativeComponent.indexOf("{revealed ? (")).toBeLessThan(narrativeComponent.indexOf("<LoginForm />"));
    expect(narrativeComponent).toContain('<a href="/login">');
    expect(narrativeComponent).toContain('<a href="/">');
  });

  it("listens for capability changes and cleans up timers, listeners, and animation frames", () => {
    expect(narrativeComponent).toContain('window.matchMedia("(prefers-reduced-motion: reduce)")');
    expect(narrativeComponent).toContain("mediaQuery.removeEventListener");
    expect(narrativeComponent).toContain('window.removeEventListener("resize"');
    expect(narrativeComponent).toContain("window.clearTimeout(completionTimer)");
    expect(narrativeComponent).toContain("window.cancelAnimationFrame");
    expect(narrativeComponent).toContain("data-quality={quality}");
  });

  it("uses five programmatic wax layers with a bounded static filter and particle count", () => {
    for (const layer of ["base", "rim", "imprint", "residue", "magic"]) {
      expect(waxSeal).toContain(`narrative-entry__wax-layer--${layer}`);
    }

    expect(waxSeal).toContain('numOctaves="2"');
    expect(waxSeal).toContain('seed="37"');
    expect(waxSeal).not.toContain("filterRes");
    const magicLayer = waxSeal.slice(waxSeal.indexOf('narrative-entry__wax-layer--magic'));
    expect(magicLayer.match(/<circle\b/g)).toHaveLength(8);
  });

  it("defines low-cost, reduced-motion, and forced-colors fallbacks", () => {
    expect(narrativeStyles).toContain('[data-quality="low"]');
    expect(narrativeStyles).toContain('[data-quality="reduced"]');
    expect(narrativeStyles).toContain("filter: none");
    expect(narrativeStyles).toContain("@media (prefers-reduced-motion: reduce)");
    expect(narrativeStyles).toContain("@media (forced-colors: active)");
    expect(narrativeStyles).toContain("min-height: 44px");
    expect(narrativeStyles).toContain("color: CanvasText");
    expect(narrativeStyles).toContain("background: Canvas");
    expect(narrativeStyles).toContain("background: ButtonFace");
    expect(narrativeStyles).toContain("outline-color: Highlight");
  });
});
