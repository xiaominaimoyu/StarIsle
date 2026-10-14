import { describe, expect, it } from "vitest";
import {
  hasPlayableTracks,
  nextPublicTheme,
  parsePublicBackgroundSettings,
  parsePublicPlayerVolume,
  parsePublicTheme,
  sortPublicRepos,
} from "./public-ui";

describe("public UI helpers", () => {
  it("normalizes theme values and toggles predictably", () => {
    expect(parsePublicTheme("light")).toBe("light");
    expect(parsePublicTheme("unexpected")).toBe("dark");
    expect(nextPublicTheme("dark")).toBe("light");
  });

  it("uses the player default only for absent, empty, invalid, or out-of-range volume storage", () => {
    expect(parsePublicPlayerVolume(null)).toBe(0.8);
    expect(parsePublicPlayerVolume("")).toBe(0.8);
    expect(parsePublicPlayerVolume("   ")).toBe(0.8);
    expect(parsePublicPlayerVolume("NaN")).toBe(0.8);
    expect(parsePublicPlayerVolume("-0.01")).toBe(0.8);
    expect(parsePublicPlayerVolume("1.01")).toBe(0.8);
    expect(parsePublicPlayerVolume("0")).toBe(0);
    expect(parsePublicPlayerVolume("0.8")).toBe(0.8);
    expect(parsePublicPlayerVolume("1")).toBe(1);
  });

  it("keeps background settings bounded and ignores stale custom blob URLs", () => {
    expect(parsePublicBackgroundSettings(null)).toEqual({
      url: null,
      custom: false,
      scrimOpacity: 0.45,
      blur: 0,
    });
    expect(parsePublicBackgroundSettings('{"url":"blob:stale","custom":true,"scrimOpacity":2,"blur":-1}')).toEqual({
      url: null,
      custom: true,
      scrimOpacity: 0.9,
      blur: 0,
    });
  });

  it("filters and stably sorts repository fixtures with missing dates", () => {
    const repos = [
      { name: "older", source: "github" as const, featured: false, updatedAt: "2024-01-01" },
      { name: "featured", source: "gitee" as const, featured: true, updatedAt: "2023-01-01" },
      { name: "missing-a", source: "github" as const, featured: false, updatedAt: "" },
      { name: "missing-b", source: "github" as const, featured: false, updatedAt: "not-a-date" },
    ];

    expect(sortPublicRepos(repos, { source: "all", sort: "featured" }).map((repo) => repo.name)).toEqual([
      "featured",
      "older",
      "missing-a",
      "missing-b",
    ]);
    expect(sortPublicRepos(repos, { source: "github", sort: "updated" }).map((repo) => repo.name)).toEqual([
      "older",
      "missing-a",
      "missing-b",
    ]);
  });

  it("hides every player affordance while the playlist is empty", () => {
    expect(hasPlayableTracks([])).toBe(false);
    expect(hasPlayableTracks([{ title: "x" }])).toBe(true);
    // A malformed snapshot must not render a dock that cannot play anything.
    expect(hasPlayableTracks(null as unknown as readonly unknown[])).toBe(false);
    expect(hasPlayableTracks(undefined as unknown as readonly unknown[])).toBe(false);
  });
});
