import { describe, expect, it } from "vitest";
import {
  activateSeal,
  cancelTear,
  completeMelt,
  completeTear,
  initialEnterNarrativeState,
  meltDurationMs,
  renderedEnterNarrativeState,
  startTear,
  tearDurationMs,
} from "./entry-state";

describe("/enter narrative state", () => {
  it("routes a seal activation only through the public exit sequence", () => {
    expect(initialEnterNarrativeState).toBe("idle");
    const sealed = renderedEnterNarrativeState();
    const melting = activateSeal(sealed);

    expect(sealed).toBe("sealed");
    expect(melting).toBe("melting");
    expect(completeMelt(melting)).toBe("publicExit");
  });

  it("routes a completed tear only through the private exit sequence", () => {
    const dragging = startTear(renderedEnterNarrativeState());

    expect(dragging).toBe("dragging");
    expect(completeTear(dragging)).toBe("privateExit");
    expect(cancelTear(dragging)).toBe("sealed");
  });

  it("does not let events cross or restart either path", () => {
    expect(startTear("melting")).toBe("melting");
    expect(activateSeal("dragging")).toBe("dragging");
    expect(completeMelt("sealed")).toBe("sealed");
    expect(completeTear("sealed")).toBe("sealed");
    expect(cancelTear("privateExit")).toBe("privateExit");
  });

  it("uses bounded normal and reduced-motion timings", () => {
    expect(meltDurationMs(false)).toBe(2200);
    expect(meltDurationMs(true)).toBe(160);
    expect(tearDurationMs(false)).toBe(1180);
    expect(tearDurationMs(true)).toBe(160);
  });
});
