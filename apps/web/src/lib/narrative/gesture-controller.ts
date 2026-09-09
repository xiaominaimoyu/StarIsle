export type TearGesturePhase = "idle" | "tracking" | "committed";
export type TearGestureAxis = "pending" | "horizontal" | "vertical";

export interface TearPointerSample {
  readonly pointerId: number;
  readonly clientX: number;
  readonly clientY: number;
  readonly timeStamp: number;
  readonly isPrimary: boolean;
}

export interface TearGestureState {
  readonly phase: TearGesturePhase;
  readonly axis: TearGestureAxis;
  readonly pointerId: number | null;
  readonly startX: number;
  readonly startY: number;
  readonly lastX: number;
  readonly lastY: number;
  readonly lastTimestamp: number;
  readonly travel: number;
  readonly rawProgress: number;
  readonly progress: number;
  readonly velocity: number;
}

export interface TearGestureConfig {
  readonly slopPx: number;
  readonly completeProgress: number;
  readonly flickProgress: number;
  readonly flickVelocity: number;
  readonly flickVelocityWindowMs: number;
  readonly velocityAlpha: number;
}

export type TearBeginOutcome = "started" | "ignored" | "cancelled";
export type TearFinishOutcome = "commit" | "rollback" | "activate" | "ignored";

export const DEFAULT_TEAR_GESTURE_CONFIG: TearGestureConfig = Object.freeze({
  slopPx: 12,
  completeProgress: 0.62,
  flickProgress: 0.45,
  flickVelocity: 0.8,
  flickVelocityWindowMs: 32,
  velocityAlpha: 0.35,
});

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function isFinitePointerSample(sample: TearPointerSample): boolean {
  return Number.isInteger(sample.pointerId)
    && Number.isFinite(sample.clientX)
    && Number.isFinite(sample.clientY)
    && Number.isFinite(sample.timeStamp);
}

export function applyTearResistance(rawProgress: number): number {
  const progress = Number.isFinite(rawProgress) ? clamp(rawProgress, 0, 1) : 0;
  return progress * (0.78 + (0.22 * progress));
}

export function createIdleTearGesture(): TearGestureState {
  return Object.freeze({
    phase: "idle",
    axis: "pending",
    pointerId: null,
    startX: 0,
    startY: 0,
    lastX: 0,
    lastY: 0,
    lastTimestamp: 0,
    travel: 0,
    rawProgress: 0,
    progress: 0,
    velocity: 0,
  });
}

export function beginTearGesture(
  state: TearGestureState,
  sample: TearPointerSample,
  travel: number,
): { readonly state: TearGestureState; readonly outcome: TearBeginOutcome } {
  if (state.phase === "tracking") {
    if (sample.pointerId !== state.pointerId) {
      return { state: createIdleTearGesture(), outcome: "cancelled" };
    }
    return { state, outcome: "ignored" };
  }
  if (state.phase === "committed" || !sample.isPrimary || !isFinitePointerSample(sample)) {
    return { state, outcome: "ignored" };
  }
  if (!Number.isFinite(travel) || travel <= 0) {
    throw new RangeError("travel must be a positive finite number");
  }

  return {
    state: Object.freeze({
      phase: "tracking",
      axis: "pending",
      pointerId: sample.pointerId,
      startX: sample.clientX,
      startY: sample.clientY,
      lastX: sample.clientX,
      lastY: sample.clientY,
      lastTimestamp: sample.timeStamp,
      travel,
      rawProgress: 0,
      progress: 0,
      velocity: 0,
    }),
    outcome: "started",
  };
}

export function moveTearGesture(
  state: TearGestureState,
  sample: TearPointerSample,
  config: TearGestureConfig = DEFAULT_TEAR_GESTURE_CONFIG,
): TearGestureState {
  if (state.phase !== "tracking" || sample.pointerId !== state.pointerId) return state;
  if (!isFinitePointerSample(sample)) return createIdleTearGesture();

  const deltaX = sample.clientX - state.startX;
  const deltaY = sample.clientY - state.startY;
  const elapsed = Math.max(1, sample.timeStamp - state.lastTimestamp);
  const instantaneousVelocity = clamp((sample.clientX - state.lastX) / elapsed, -2, 2);
  const elapsedFrames = Math.max(1, elapsed / 16);
  const velocityAlpha = 1 - Math.pow(1 - config.velocityAlpha, elapsedFrames);
  const velocity = clamp(
    state.velocity + (velocityAlpha * (instantaneousVelocity - state.velocity)),
    -2,
    2,
  );
  let axis = state.axis;
  if (
    axis === "pending"
    && Math.max(Math.abs(deltaX), Math.abs(deltaY)) >= config.slopPx
  ) {
    axis = Math.abs(deltaX) >= Math.abs(deltaY) ? "horizontal" : "vertical";
  }
  const rawProgress = axis === "horizontal" ? clamp(deltaX / state.travel, 0, 1) : 0;

  return Object.freeze({
    ...state,
    axis,
    lastX: sample.clientX,
    lastY: sample.clientY,
    lastTimestamp: sample.timeStamp,
    rawProgress,
    progress: applyTearResistance(rawProgress),
    velocity,
  });
}

export function finishTearGesture(
  state: TearGestureState,
  sample: TearPointerSample,
  config: TearGestureConfig = DEFAULT_TEAR_GESTURE_CONFIG,
): { readonly state: TearGestureState; readonly outcome: TearFinishOutcome } {
  if (state.phase !== "tracking" || sample.pointerId !== state.pointerId) {
    return { state, outcome: "ignored" };
  }

  const elapsedSinceMove = sample.timeStamp - state.lastTimestamp;
  const unchangedPosition = sample.clientX === state.lastX && sample.clientY === state.lastY;
  const keepsRecentVelocity = unchangedPosition
    && elapsedSinceMove >= 0
    && elapsedSinceMove <= config.flickVelocityWindowMs;
  let finalState = keepsRecentVelocity ? state : moveTearGesture(state, sample, config);
  if (unchangedPosition && !keepsRecentVelocity && finalState.phase === "tracking") {
    finalState = Object.freeze({ ...finalState, velocity: 0 });
  }
  if (finalState.phase !== "tracking") {
    return { state: createIdleTearGesture(), outcome: "rollback" };
  }
  if (finalState.axis === "pending") {
    return { state: createIdleTearGesture(), outcome: "activate" };
  }
  const shouldCommit = finalState.axis === "horizontal" && (
    finalState.rawProgress >= config.completeProgress
    || (
      finalState.rawProgress >= config.flickProgress
      && finalState.velocity >= config.flickVelocity
    )
  );
  if (!shouldCommit) {
    return { state: createIdleTearGesture(), outcome: "rollback" };
  }

  return {
    state: Object.freeze({
      ...finalState,
      phase: "committed",
      rawProgress: 1,
      progress: 1,
    }),
    outcome: "commit",
  };
}

export function cancelTearGesture(_state: TearGestureState): TearGestureState {
  return createIdleTearGesture();
}
