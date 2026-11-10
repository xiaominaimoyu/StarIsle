import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./global.css", import.meta.url), "utf8");
const login = readFileSync(new URL("../pages/login.astro", import.meta.url), "utf8");

describe("public visual polish", () => {
  it("uses the same stacked mark on the private-entry header", () => {
    expect(login).toContain("brand-lockup__text");
    expect(login).toContain("<strong>星屿</strong>");
    expect(login).not.toContain("backdrop-blur");
  });

  it("keeps light secondary text, prose rhythm, and reduced motion", () => {
    expect(css).toContain("--text-dim: #5c4d3a");
    expect(css).toContain("text-wrap: pretty");
    expect(css).toContain(".resume-contact a");
    expect(css).toMatch(/prefers-reduced-motion: reduce\)[\s\S]*transform:\s*none/);
    expect(css).toContain(':root[data-theme="light"] .public-header');
    expect(css).toMatch(/\.auth-frame__context p \{[^}]*0\.75rem/);
    expect(css).toMatch(/\.auth-letter__eyebrow \{[^}]*0\.75rem/);
  });
});
