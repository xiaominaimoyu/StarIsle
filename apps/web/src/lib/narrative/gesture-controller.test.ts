import { describe, expect, it } from "vitest";
import {
  DEFAULT_TEAR_GESTURE_CONFIG,
  applyTearResistance,
  beginTearGesture,
  cancelTearGesture,
  createIdleTearGesture,
  finishTearGesture,
  moveTearGesture,
  type TearPointerSample,
} from "./gesture-controller";

function pointer(
  pointerId: number,
  clientX: number,
  clientY: number,
  timeStamp: number,
  isPrimary = true,
): TearPointerSample {
  return { pointerId, clientX, clientY, timeStamp, isPrimary };
}

describe("tear gesture controller", () => {
  it("starts only one finite primary pointer", () => {
    const idle = createIdleTearGesture();
    const ignored = beginTearGesture(idle, pointer(7, 10, 20, 0, false), 200);
    const started = beginTearGesture(idle, pointer(7, 10, 20, 0), 200);

    expect(ignored.outcome).toBe("ignored");
    expect(ignored.state).toBe(idle);
    expect(started.outcome).toBe("started");
    expect(started.state).toMatchObject({
      phase: "tracking",
      axis: "pending",
      pointerId: 7,
      startX: 10,
      startY: 20,
      progress: 0,
      rawProgress: 0,
    });
  });

  it("waits for slop, then locks a horizontal gesture to canonical progress", () => {
    const started = beginTearGesture(
      createIdleTearGesture(),
      pointer(1, 100, 40, 10),
      200,
    ).state;
    const pending = moveTearGesture(started, pointer(1, 108, 44, 20));
    const horizontal = moveTearGesture(pending, pointer(1, 150, 46, 40));

    expect(pending.axis).toBe("pending");
    expect(pending.progress).toBe(0);
    expect(horizontal.axis).toBe("horizontal");
    expect(horizontal.rawProgress).toBe(0.25);
    expect(horizontal.progress).toBe(applyTearResistance(0.25));
    expect(horizontal.velocity).toBeGreaterThan(0);
  });

  it("locks vertical intent without driving progress and rolls it back", () => {
    const started = beginTearGesture(
      createIdleTearGesture(),
      pointer(2, 20, 20, 0),
      180,
    ).state;
    const vertical = moveTearGesture(started, pointer(2, 24, 50, 20));
    const finished = finishTearGesture(vertical, pointer(2, 25, 70, 40));

    expect(vertical.axis).toBe("vertical");
    expect(vertical.progress).toBe(0);
    expect(finished.outcome).toBe("rollback");
    expect(finished.state).toEqual(createIdleTearGesture());
  });

  it("ignores events from a pointer that does not own the gesture", () => {
    const started = beginTearGesture(
      createIdleTearGesture(),
      pointer(3, 0, 0, 0),
      200,
    ).state;

    expect(moveTearGesture(started, pointer(4, 150, 0, 20))).toBe(started);
    expect(finishTearGesture(started, pointer(4, 150, 0, 20))).toEqual({
      state: started,
      outcome: "ignored",
    });
  });

  it("cancels the active gesture when a second pointer appears", () => {
    const started = beginTearGesture(
      createIdleTearGesture(),
      pointer(5, 0, 0, 0),
      200,
    ).state;
    const second = beginTearGesture(started, pointer(6, 20, 0, 10, false), 200);

    expect(second.outcome).toBe("cancelled");
    expect(second.state).toEqual(createIdleTearGesture());
  });

  it("commits at the progress threshold and otherwise rolls back", () => {
    const started = beginTearGesture(
      createIdleTearGesture(),
      pointer(7, 0, 0, 0),
      200,
    ).state;
    const commit = finishTearGesture(started, pointer(7, 124, 2, 500));
    const rollback = finishTearGesture(started, pointer(7, 80, 2, 500));

    expect(commit.outcome).toBe("commit");
    expect(commit.state).toMatchObject({ phase: "committed", progress: 1, rawProgress: 1 });
    expect(rollback.outcome).toBe("rollback");
    expect(rollback.state.phase).toBe("idle");
  });

  it("allows a deliberate fast flick only after the minimum progress", () => {
    const started = beginTearGesture(
      createIdleTearGesture(),
      pointer(8, 0, 0, 0),
      200,
    ).state;
    const fastFirst = moveTearGesture(started, pointer(8, 90, 1, 10));
    const fastSecond = moveTearGesture(fastFirst, pointer(8, 100, 1, 20));
    const fastFinish = finishTearGesture(fastSecond, pointer(8, 100, 1, 21));

    const shortFirst = moveTearGesture(started, pointer(8, 70, 1, 10));
    const shortSecond = moveTearGesture(shortFirst, pointer(8, 80, 1, 20));
    const shortFinish = finishTearGesture(shortSecond, pointer(8, 80, 1, 21));

    expect(fastSecond.rawProgress).toBe(0.5);
    expect(fastSecond.velocity).toBeGreaterThanOrEqual(0.8);
    expect(fastFinish.outcome).toBe("commit");
    expect(shortSecond.rawProgress).toBe(0.4);
    expect(shortFinish.outcome).toBe("rollback");
  });

  it("uses inclusive completion, flick-progress, and flick-velocity boundaries", () => {
    const completeOnlyConfig = {
      ...DEFAULT_TEAR_GESTURE_CONFIG,
      flickProgress: 1,
      flickVelocity: 2,
      velocityAlpha: 1,
    };
    const completeStarted = beginTearGesture(
      createIdleTearGesture(),
      pointer(14, 0, 0, 0),
      10_000,
    ).state;
    const belowComplete = moveTearGesture(
      completeStarted,
      pointer(14, 6_199, 0, 1_000),
      completeOnlyConfig,
    );
    const atComplete = moveTearGesture(
      completeStarted,
      pointer(14, 6_200, 0, 1_000),
      completeOnlyConfig,
    );

    expect(finishTearGesture(
      belowComplete,
      pointer(14, 6_199, 0, 1_001),
      completeOnlyConfig,
    ).outcome).toBe("rollback");
    expect(finishTearGesture(
      atComplete,
      pointer(14, 6_200, 0, 1_001),
      completeOnlyConfig,
    ).outcome).toBe("commit");

    const flickConfig = {
      ...DEFAULT_TEAR_GESTURE_CONFIG,
      completeProgress: 1,
      velocityAlpha: 1,
    };
    const flickStarted = beginTearGesture(
      createIdleTearGesture(),
      pointer(15, 0, 0, 0),
      1_000,
    ).state;
    const belowFlickProgress = moveTearGesture(
      flickStarted,
      pointer(15, 449, 0, 500),
      flickConfig,
    );
    const atFlickProgress = moveTearGesture(
      flickStarted,
      pointer(15, 450, 0, 500),
      flickConfig,
    );

    expect(finishTearGesture(
      belowFlickProgress,
      pointer(15, 449, 0, 501),
      flickConfig,
    ).outcome).toBe("rollback");
    expect(finishTearGesture(
      { ...atFlickProgress, velocity: 0.799 },
      pointer(15, 450, 0, 501),
      flickConfig,
    ).outcome).toBe("rollback");
    expect(finishTearGesture(
      { ...atFlickProgress, velocity: 0.8 },
      pointer(15, 450, 0, 501),
      flickConfig,
    ).outcome).toBe("commit");
  });

  it("expires a flick velocity after the pointer stops", () => {
    const started = beginTearGesture(
      createIdleTearGesture(),
      pointer(13, 0, 0, 0),
      200,
    ).state;
    const fastFirst = moveTearGesture(started, pointer(13, 90, 1, 10));
    const fastSecond = moveTearGesture(fastFirst, pointer(13, 100, 1, 20));
    const stopped = finishTearGesture(fastSecond, pointer(13, 100, 1, 80));

    expect(fastSecond.velocity).toBeGreaterThanOrEqual(0.8);
    expect(stopped.outcome).toBe("rollback");
  });

  it("decays stale velocity when the pointer resumes after a pause", () => {
    const started = beginTearGesture(
      createIdleTearGesture(),
      pointer(16, 0, 0, 0),
      200,
    ).state;
    const fastFirst = moveTearGesture(started, pointer(16, 90, 1, 10));
    const fastSecond = moveTearGesture(fastFirst, pointer(16, 100, 1, 20));
    const resumed = moveTearGesture(fastSecond, pointer(16, 110, 1, 200));
    const finished = finishTearGesture(resumed, pointer(16, 110, 1, 201));

    expect(fastSecond.velocity).toBeGreaterThanOrEqual(0.8);
    expect(resumed.velocity).toBeLessThan(DEFAULT_TEAR_GESTURE_CONFIG.flickVelocity);
    expect(resumed.rawProgress).toBe(0.55);
    expect(finished.outcome).toBe("rollback");
  });

  it("leaves a tap for the semantic control click path", () => {
    const started = beginTearGesture(
      createIdleTearGesture(),
      pointer(9, 20, 20, 0),
      200,
    ).state;
    const finished = finishTearGesture(started, pointer(9, 24, 22, 30));

    expect(finished.outcome).toBe("activate");
    expect(finished.state).toEqual(createIdleTearGesture());
  });

  it("clamps reverse/overshoot movement and non-monotonic timestamps safely", () => {
    const started = beginTearGesture(
      createIdleTearGesture(),
      pointer(10, 100, 0, 50),
      200,
    ).state;
    const reverse = moveTearGesture(started, pointer(10, 50, 0, 40));
    const overshoot = moveTearGesture(reverse, pointer(10, 500, 0, 40));

    expect(reverse.rawProgress).toBe(0);
    expect(reverse.velocity).toBeGreaterThanOrEqual(-2);
    expect(overshoot.rawProgress).toBe(1);
    expect(overshoot.progress).toBe(1);
    expect(Number.isFinite(overshoot.velocity)).toBe(true);
  });

  it("cancels invalid active samples and every explicit cancellation source", () => {
    const started = beginTearGesture(
      createIdleTearGesture(),
      pointer(11, 0, 0, 0),
      200,
    ).state;
    const invalid = moveTearGesture(started, pointer(11, Number.NaN, 0, 20));

    expect(invalid).toEqual(createIdleTearGesture());
    expect(cancelTearGesture(started)).toEqual(createIdleTearGesture());
    expect(cancelTearGesture({ ...started, phase: "committed" })).toEqual(
      createIdleTearGesture(),
    );
  });

  it("uses a monotonic resistance curve with stable endpoints", () => {
    const samples = [0, 0.1, 0.25, 0.5, 0.75, 1].map(applyTearResistance);

    expect(samples[0]).toBe(0);
    expect(samples.at(-1)).toBe(1);
    expect(samples).toEqual([...samples].sort((left, right) => left - right));
    expect(applyTearResistance(-1)).toBe(0);
    expect(applyTearResistance(Number.NaN)).toBe(0);
    expect(applyTearResistance(2)).toBe(1);
  });

  it("rejects a non-positive travel distance at gesture start", () => {
    expect(() => beginTearGesture(
      createIdleTearGesture(),
      pointer(12, 0, 0, 0),
      0,
    )).toThrow(RangeError);
  });
});
