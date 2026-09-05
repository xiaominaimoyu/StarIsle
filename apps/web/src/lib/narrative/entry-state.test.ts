import { describe, expect, it } from "vitest";
import {
  activateSeal,
  completeMelt,
  initialEnterNarrativeState,
  meltDurationMs,
} from "./entry-state";

describe("/enter narrative state", () => {
  it("moves only from sealed through melting to loginReveal", () => {
    const melting = activateSeal(initialEnterNarrativeState);

    expect(melting).toBe("melting");
    expect(completeMelt(melting)).toBe("loginReveal");
  });

  it("does not let repeated events skip or restart the sequence", () => {
    expect(activateSeal("melting")).toBe("melting");
    expect(activateSeal("loginReveal")).toBe("loginReveal");
    expect(completeMelt("sealed")).toBe("sealed");
    expect(completeMelt("loginReveal")).toBe("loginReveal");
  });

  it("uses the approved normal and reduced-motion timing candidates", () => {
    expect(meltDurationMs(false)).toBe(2600);
    expect(meltDurationMs(true)).toBe(140);
  });
});
