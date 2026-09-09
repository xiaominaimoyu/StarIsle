import { describe, expect, it } from "vitest";
import { lowerNarrativeQuality, selectNarrativeQuality } from "./quality";

describe("narrative quality selection", () => {
  it("treats reduced motion and save-data as hard downgrades", () => {
    expect(selectNarrativeQuality({
      reducedMotion: true,
      saveData: false,
      viewportWidth: 1440,
      deviceMemory: 16,
      measuredFps: 60,
    })).toBe("reduced");
    expect(selectNarrativeQuality({
      reducedMotion: false,
      saveData: true,
      viewportWidth: 1440,
      deviceMemory: 16,
      measuredFps: 60,
    })).toBe("low");
  });

  it("uses viewport, optional memory, and measured fps without UA sniffing", () => {
    expect(selectNarrativeQuality({
      reducedMotion: false,
      saveData: false,
      viewportWidth: 1200,
      deviceMemory: 8,
      measuredFps: 60,
    })).toBe("high");
    expect(selectNarrativeQuality({
      reducedMotion: false,
      saveData: false,
      viewportWidth: 800,
      deviceMemory: 8,
      measuredFps: 60,
    })).toBe("medium");
    expect(selectNarrativeQuality({
      reducedMotion: false,
      saveData: false,
      viewportWidth: 1200,
      deviceMemory: 2,
      measuredFps: 60,
    })).toBe("low");
  });

  it("allows runtime evidence to lower but never silently raise a tier", () => {
    expect(lowerNarrativeQuality("high", "medium")).toBe("medium");
    expect(lowerNarrativeQuality("low", "high")).toBe("low");
    expect(lowerNarrativeQuality("medium", "medium")).toBe("medium");
  });
});
