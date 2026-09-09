import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { createTearGeometry, projectTearGeometry } from "../../lib/narrative/tear-geometry";
import TearMask from "./TearMask";

describe("TearMask", () => {
  it("renders an alpha mask and tightly bounded high-tier fiber filter", () => {
    const markup = renderToStaticMarkup(createElement(TearMask, {
      quality: "high",
      progress: 0.5,
    }));

    expect(markup).toContain('maskUnits="userSpaceOnUse"');
    expect(markup).toContain('maskContentUnits="userSpaceOnUse"');
    expect(markup).toMatch(/<mask[^>]+style="[^"]*mask-type:alpha[^"]*"/);
    expect(markup).toContain('filterUnits="userSpaceOnUse"');
    expect(markup).toContain('primitiveUnits="userSpaceOnUse"');
    expect(markup).toContain('color-interpolation-filters="sRGB"');
    expect(markup).toContain("<feTurbulence");
    expect(markup).toContain("<feDisplacementMap");
    expect(markup).toContain('data-filter-mode="fiber"');
    expect(markup.match(/stroke-dashoffset="0.5"/g)).toHaveLength(2);

    const pathTags = [...markup.matchAll(/<path[^>]+>/g)].map((match) => match[0]);
    const paths = pathTags.map((tag) => tag.match(/d="([^"]+)"/)?.[1]);
    expect(paths).toHaveLength(3);
    expect(new Set(paths).size).toBe(1);
    const openingStart = markup.indexOf('class="narrative-tear-mask__opening"');
    const openingEnd = markup.indexOf("</g>", openingStart);
    const opening = markup.slice(openingStart, openingEnd);
    expect(opening).not.toContain("stroke-dasharray");
    expect(opening).not.toContain("stroke-dashoffset");

    const filterHeight = Number(markup.match(/<filter[^>]+height="([\d.]+)"/)?.[1]);
    expect(filterHeight).toBeGreaterThan(0);
    expect(filterHeight).toBeLessThan(240 * 0.6);
  });

  it.each(["medium", "low", "reduced"] as const)(
    "keeps the %s tier on the unfiltered outline fallback",
    (quality) => {
      const markup = renderToStaticMarkup(createElement(TearMask, { quality }));

      expect(markup).toContain(`data-quality="${quality}"`);
      expect(markup).toContain('data-filter-mode="outline"');
      expect(markup).toContain('class="narrative-tear-mask__outline"');
      expect(markup).not.toContain("<filter");
      expect(markup).not.toContain("<feTurbulence");
    },
  );

  it("clamps progress without exposing invalid SVG values", () => {
    const beforeStart = renderToStaticMarkup(createElement(TearMask, { progress: Number.NaN }));
    const afterEnd = renderToStaticMarkup(createElement(TearMask, { progress: 4 }));

    expect(beforeStart).toContain('stroke-dashoffset="1"');
    expect(beforeStart).not.toContain("NaN");
    expect(afterEnd).toContain('stroke-dashoffset="0"');
  });

  it("uses the exported canonical geometry and projected path by default", () => {
    const expectedPath = projectTearGeometry(createTearGeometry(), 1000, 240).path;
    const markup = renderToStaticMarkup(createElement(TearMask));

    expect(markup).toContain(`d="${expectedPath}"`);
  });

  it("can reveal an injected SVG surface through the same progressive mask", () => {
    const markup = renderToStaticMarkup(createElement(
      TearMask,
      { progress: 0.5 },
      createElement("rect", {
        className: "fixture-opening-surface",
        x: 0,
        y: 0,
        width: 1000,
        height: 240,
      }),
    ));

    expect(markup).toContain('class="fixture-opening-surface"');
    expect(markup).toMatch(/<g[^>]+class="narrative-tear-mask__opening"[^>]+mask="url\(#[^)]+\)"/);
    expect(markup.match(/stroke-dashoffset="0.5"/g)).toHaveLength(2);
  });

  it("uses collision-free IDs when multiple static layers share a document", () => {
    const markup = renderToStaticMarkup(createElement(
      Fragment,
      null,
      createElement(TearMask, { quality: "high" }),
      createElement(TearMask, { quality: "high" }),
    ));
    const svgMarkup = markup.match(/<svg[\s\S]*?<\/svg>/g) ?? [];
    const maskIds = svgMarkup.map((svg) => (
      svg.match(/id="(narrative-tear-mask-[^"]+)"/)?.[1]
    ));
    const filterIds = svgMarkup.map((svg) => (
      svg.match(/id="(narrative-tear-filter-[^"]+)"/)?.[1]
    ));

    expect(svgMarkup).toHaveLength(2);
    expect(maskIds).toHaveLength(2);
    expect(new Set(maskIds).size).toBe(2);
    expect(filterIds).toHaveLength(2);
    expect(new Set(filterIds).size).toBe(2);
    for (const [index, svg] of svgMarkup.entries()) {
      const localMaskId = maskIds[index];
      const localFilterId = filterIds[index];
      expect(localMaskId).toBeDefined();
      expect(localFilterId).toBeDefined();
      expect(svg).toContain(`mask="url(#${localMaskId})"`);
      expect(svg).toContain(`filter="url(#${localFilterId})"`);
      for (const foreignId of [...maskIds, ...filterIds].filter(
        (id) => id !== localMaskId && id !== localFilterId,
      )) {
        expect(svg).not.toContain(`url(#${foreignId})`);
      }
    }
  });

  it("stays decorative and non-interactive", async () => {
    const { readFile } = await import("node:fs/promises");
    const [source, styles] = await Promise.all([
      readFile(new URL("./TearMask.tsx", import.meta.url), "utf8"),
      readFile(new URL("./tear-mask.css", import.meta.url), "utf8"),
    ]);

    expect(source).toContain('aria-hidden="true"');
    expect(source).toContain('focusable="false"');
    expect(source).toContain('pointerEvents: "none"');
    for (const forbidden of [
      "onPointer",
      "<canvas",
      "AudioManager",
      "window.",
      "location.",
      "fetch(",
      "navigate(",
    ]) {
      expect(source).not.toContain(forbidden);
    }
    expect(styles).toContain("@media (forced-colors: active)");
    expect(styles).toContain("filter: none !important");
    expect(styles).not.toContain("animation:");
  });
});
