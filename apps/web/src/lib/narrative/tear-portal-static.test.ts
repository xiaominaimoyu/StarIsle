import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import TearPortalLink from "../../components/narrative/TearPortalLink";

const component = readFileSync(
  new URL("../../components/narrative/TearPortalLink.tsx", import.meta.url),
  "utf8",
);
const styles = readFileSync(
  new URL("../../components/narrative/tear-portal-link.css", import.meta.url),
  "utf8",
);
const debrisStyles = readFileSync(
  new URL("../../components/narrative/tear-debris.css", import.meta.url),
  "utf8",
);
const markupForGuards = renderToStaticMarkup(createElement(TearPortalLink));
const gate = readFileSync(
  new URL("../../components/auth/ConsoleSessionGate.tsx", import.meta.url),
  "utf8",
);
const consoleLayout = readFileSync(new URL("../../layouts/Console.astro", import.meta.url), "utf8");
const consolePages = ["index", "gallery", "files", "mail", "kb"].map((name) => ({
  name,
  source: readFileSync(new URL(`../../pages/console/${name}.astro`, import.meta.url), "utf8"),
}));

describe("Console TearPortalLink isolation guards", () => {
  it("renders a semantic 56px control and an independent native fallback link", () => {
    const markup = renderToStaticMarkup(createElement(TearPortalLink));

    expect(markup).toContain('aria-label="返回公开门户"');
    expect(markup).toContain('aria-label="向右撕开纸页前往星"');
    expect(markup).toContain('aria-describedby="');
    expect(markup).toContain('<a href="/">普通前往星</a>');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain('data-phase="isleIdle"');
    expect(markup).toContain('data-motion="full"');
  });

  it("keeps the enhancement behind SessionGate and in its own dynamic chunk", () => {
    expect(gate).toContain('lazy(() => import("../narrative/TearPortalLink"))');
    expect(gate.indexOf("<SessionGate>")).toBeLessThan(gate.indexOf("<TearPortalLink />"));
    expect(gate.indexOf("<TearPortalLink />")).toBeLessThan(gate.indexOf("</SessionGate>"));
    for (const page of consolePages) {
      expect(page.source, page.name).toContain('import ConsoleSessionGate from "@/components/auth/ConsoleSessionGate"');
      expect(page.source, page.name).toContain("<ConsoleSessionGate client:load>");
      expect(page.source, page.name).toContain("</ConsoleSessionGate>");
    }
  });

  it("keeps native portal links available outside the authenticated island", () => {
    expect(consoleLayout.match(/href="\/"/g)?.length).toBeGreaterThanOrEqual(3);
    expect(consoleLayout).toContain('aria-label="前往公开门户"');
  });

  it("implements one captured pointer, coalesced rendering, cancellation, and one native navigation", () => {
    expect(component).toContain("setPointerCapture");
    expect(component).toContain("hasPointerCapture");
    expect(component).toContain("releasePointerCapture");
    expect(component).toContain("requestAnimationFrame");
    expect(component).toContain('window.addEventListener("pointerdown"');
    expect(component).toContain('window.addEventListener("resize"');
    expect(component).toContain('document.addEventListener("visibilitychange"');
    expect(component).toContain('event.key !== "Escape"');
    expect(component).toContain('window.location.assign("/")');
    expect(component).toContain("navigationScheduledRef.current");
    expect(component).toContain("<TearMask progress={view.progress}");
  });

  it("mounts the shared fleck layer without dragging in the envelope, audio or auth bundle", () => {
    expect(component).toContain("<TearDebrisCanvas");
    expect(debrisStyles).toContain(".narrative-tear-debris");
    expect(debrisStyles).toContain("pointer-events: none");
    expect(component).not.toMatch(/from\s+["'][^"']*(?:session|api|audio|player|NarrativeEntry|entry-state|WaxSeal|narrative-entry)[^"']*["']/i);
    expect(component).not.toMatch(/from\s+["'][^"']*tear-debris-canvas["']/i);
    expect(component).not.toContain("fetch(");
    expect(component).not.toContain("AudioContext");
    expect(component).not.toContain("ClientRouter");
    // The flecks are decorative only; the panel keeps one semantic control and one native link.
    expect(markupForGuards.match(/<canvas/g) ?? []).toHaveLength(1);
    expect(markupForGuards.match(/<a href="\/"/g) ?? []).toHaveLength(1);
  });

  it("preserves scroll/zoom and defines reduced-motion and forced-colors fallbacks", () => {
    const secondPointerHandlerStart = component.indexOf("const cancelForSecondPointer");
    const secondPointerHandlerEnd = component.indexOf("\n    };", secondPointerHandlerStart);
    const secondPointerHandler = component.slice(
      secondPointerHandlerStart,
      secondPointerHandlerEnd,
    );

    expect(secondPointerHandlerStart).toBeGreaterThanOrEqual(0);
    expect(secondPointerHandlerEnd).toBeGreaterThan(secondPointerHandlerStart);
    expect(secondPointerHandler).not.toContain("event.preventDefault()");
    expect(styles).toContain("width: 56px");
    expect(styles).toContain("height: 56px");
    expect(styles).toContain("min-height: 44px");
    expect(styles).toContain("touch-action: pan-y pinch-zoom");
    expect(styles).toContain("@media (prefers-reduced-motion: reduce)");
    expect(styles).toContain("@media (forced-colors: active)");
    expect(styles).toContain("outline-color: Highlight");
  });
});
