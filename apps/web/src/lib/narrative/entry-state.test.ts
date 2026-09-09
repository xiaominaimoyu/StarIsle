import { describe, expect, it } from "vitest";
import {
  acknowledgeLoginSuccess,
  activateSeal,
  completeMelt,
  initialEnterNarrativeState,
  meltDurationMs,
  renderedEnterNarrativeState,
  reportLoginFailure,
} from "./entry-state";

describe("/enter narrative state", () => {
  it("moves only from idle through the rendered and revealed states", () => {
    expect(initialEnterNarrativeState).toBe("idle");
    const sealed = renderedEnterNarrativeState();
    const melting = activateSeal(sealed);

    expect(sealed).toBe("sealed");
    expect(melting).toBe("melting");
    expect(completeMelt(melting)).toBe("loginReveal");
  });

  it("does not let repeated events skip or restart the sequence", () => {
    expect(activateSeal("melting")).toBe("melting");
    expect(activateSeal("loginReveal")).toBe("loginReveal");
    expect(completeMelt("idle")).toBe("idle");
    expect(completeMelt("sealed")).toBe("sealed");
    expect(completeMelt("loginReveal")).toBe("loginReveal");
  });

  it("keeps login outcomes presentation-only and ordered", () => {
    expect(reportLoginFailure("loginReveal")).toBe("loginReveal");
    expect(acknowledgeLoginSuccess("sealed")).toBe("sealed");
    expect(acknowledgeLoginSuccess("loginReveal")).toBe("isle");
  });

  it("uses the approved normal and reduced-motion timing candidates", () => {
    expect(meltDurationMs(false)).toBe(2600);
    expect(meltDurationMs(true)).toBe(140);
  });
});
