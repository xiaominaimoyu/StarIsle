import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { getPublicSiteUrl, renderSitemapXml } from "../seo";

const enterPage = readFileSync(new URL("../../pages/enter.astro", import.meta.url), "utf8");
const narrativeComponent = readFileSync(new URL("../../components/narrative/NarrativeEntry.tsx", import.meta.url), "utf8");
const waxSeal = readFileSync(new URL("../../components/narrative/WaxSealSvg.tsx", import.meta.url), "utf8");
const narrativeStyles = readFileSync(new URL("../../components/narrative/narrative-entry.css", import.meta.url), "utf8");
const deskAsset = new URL("../../../public/entry/starisle-desk.webp", import.meta.url);
const envelopeAsset = new URL("../../../public/entry/starisle-envelope.webp", import.meta.url);
const sealMonogram = readFileSync(new URL("../../components/narrative/SealMonogramSvg.tsx", import.meta.url), "utf8");
const removedReferenceAsset = new URL("../../../public/entry/obsidian-envelope-sealed.jpg", import.meta.url);

describe("/enter static and isolation guards", () => {
  it("uses a noindex, chrome-free, single-envelope shell without visible intro copy", () => {
    expect(enterPage).toContain("noindex");
    expect(enterPage).toContain("chrome={false}");
    expect(enterPage).toContain('class="entry-page entry-page--immersive"');
    expect(enterPage).toContain("<NarrativeEntry client:load />");
    expect(enterPage).not.toContain("entry-intro");
    expect(enterPage).not.toContain("<nav");
  });

  it("keeps the route out of the explicit public sitemap", () => {
    expect(renderSitemapXml(getPublicSiteUrl("https://example.test"), { lastmod: "2026-09-04T00:00:00.000Z" })).not.toContain("/enter");
  });

  it("layers a rosewood desk plate under a separate envelope plate", () => {
    expect(existsSync(deskAsset)).toBe(true);
    expect(existsSync(envelopeAsset)).toBe(true);
    expect(existsSync(removedReferenceAsset)).toBe(false);
    expect(narrativeComponent).toContain('const deskAsset = "/entry/starisle-desk.webp"');
    expect(narrativeComponent).toContain('const envelopeAsset = "/entry/starisle-envelope.webp"');
    expect(narrativeStyles).toContain('url("/entry/starisle-desk.webp")');
    // One shared desk backdrop means the tear never cross-fades two different scenes.
    expect(narrativeComponent).not.toContain("starisle-velvet-empty");
    expect(narrativeComponent).not.toContain("starisle-envelope-sealed.png");
  });

  it("draws the wax seal as reusable SMY cipher art rather than a baked plate", () => {
    expect(narrativeComponent).toContain("<SealMonogramSvg");
    expect(narrativeComponent).toContain("narrative-entry__seal-face");
    for (const letter of ["M 40.5 30 C 30.5 27.5", "M 40.5 66.5 L 40.5 30", "M 56.5 30 L 66.5 48"]) {
      expect(sealMonogram, letter).toContain(letter);
    }
    expect(sealMonogram).toContain('variant = "seal"');
    expect(sealMonogram).toContain("gradientUnits=\"userSpaceOnUse\"");
    expect(narrativeStyles).toContain("narrative-wax-face-melt");
  });

  it("keeps normal links while routing the two enhanced interactions separately", () => {
    expect(narrativeComponent).toContain('href="/"');
    expect(narrativeComponent).toContain('href="/login"');
    expect(narrativeComponent).toContain('new CustomEvent("narrative:public-enter"');
    expect(narrativeComponent).toContain('new CustomEvent("narrative:private-enter"');
    expect(enterPage).toContain('window.addEventListener("narrative:public-enter"');
    expect(enterPage).toContain('window.addEventListener("narrative:private-enter"');
    expect(enterPage).not.toContain('fetch("/api/v1/auth/me"');
    expect(narrativeComponent).not.toContain("LoginForm");
    expect(narrativeComponent).not.toContain("localStorage");
  });

  it("keeps both entrances semantic, keyboard accessible, and gesture bounded", () => {
    expect(narrativeComponent).toContain('aria-label="融化封蜡，进入公开页"');
    expect(narrativeComponent).toContain('aria-label="撕开信封，进入私人登录页"');
    expect(narrativeComponent).toContain('aria-live="polite"');
    expect(narrativeComponent).toContain("setPointerCapture");
    expect(narrativeComponent).toContain("releasePointerCapture");
    expect(narrativeComponent).toContain("tearThreshold = 0.72");
    expect(narrativeComponent).toContain('event.key !== "Enter"');
  });

  it("listens for capability changes and cleans up timers, listeners, and animation frames", () => {
    expect(narrativeComponent).toContain('window.matchMedia("(prefers-reduced-motion: reduce)")');
    expect(narrativeComponent).toContain("mediaQuery.removeEventListener");
    expect(narrativeComponent).toContain('window.removeEventListener("resize"');
    expect(narrativeComponent).toContain("window.clearTimeout(completionTimer)");
    expect(narrativeComponent).toContain("window.cancelAnimationFrame");
    expect(narrativeComponent).toContain("data-quality={quality}");
  });

  it("uses a heat-softened wax pool with bounded filters", () => {
    for (const layer of ["heat", "pool", "highlights"]) {
      expect(waxSeal).toContain(`narrative-entry__wax-layer--${layer}`);
    }

    expect(waxSeal).toContain('numOctaves="1"');
    expect(waxSeal).toContain('seed="41"');
    expect(waxSeal).not.toContain("filterRes");
    expect(waxSeal).not.toContain("narrative-entry__wax-drop");
    expect(waxSeal).not.toContain("narrative-entry__wax-rivulet");
    expect(narrativeComponent).toContain('seed="29"');
  });

  it("defines low-cost, reduced-motion, and forced-colors fallbacks", () => {
    expect(narrativeStyles).toContain('[data-quality="low"]');
    expect(narrativeStyles).toContain('[data-quality="reduced"]');
    expect(narrativeStyles).toContain("filter: none");
    expect(narrativeStyles).toContain("@media (prefers-reduced-motion: reduce)");
    expect(narrativeStyles).toContain("@media (forced-colors: active)");
    expect(narrativeStyles).toContain("min-height: 44px");
    expect(narrativeStyles).toContain("CanvasText");
    expect(narrativeStyles).toContain("ButtonText");
  });
});
