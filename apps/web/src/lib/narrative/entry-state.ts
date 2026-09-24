/**
 * Presentation-only state for the /enter route.
 *
 * The seal path exits to the public site. The tear path exits to the existing
 * login route. Authentication and navigation remain outside this state
 * machine so the React island cannot grant or infer a session.
 */
export type EnterNarrativeState =
  | "idle"
  | "sealed"
  | "melting"
  | "dragging"
  | "publicExit"
  | "privateExit";

export type EnterNarrativeEvent =
  | "render"
  | "activateSeal"
  | "meltComplete"
  | "startTear"
  | "cancelTear"
  | "tearComplete";

export const initialEnterNarrativeState: EnterNarrativeState = "idle";

export function transitionEnterNarrative(
  state: EnterNarrativeState,
  event: EnterNarrativeEvent,
): EnterNarrativeState {
  if (state === "idle" && event === "render") return "sealed";
  if (state === "sealed" && event === "activateSeal") return "melting";
  if (state === "melting" && event === "meltComplete") return "publicExit";
  if (state === "sealed" && event === "startTear") return "dragging";
  if (state === "dragging" && event === "cancelTear") return "sealed";
  if (state === "dragging" && event === "tearComplete") return "privateExit";
  return state;
}

/** React SSR has already crossed the conceptual render boundary. */
export function renderedEnterNarrativeState(): EnterNarrativeState {
  return transitionEnterNarrative(initialEnterNarrativeState, "render");
}

export function activateSeal(state: EnterNarrativeState): EnterNarrativeState {
  return transitionEnterNarrative(state, "activateSeal");
}

export function completeMelt(state: EnterNarrativeState): EnterNarrativeState {
  return transitionEnterNarrative(state, "meltComplete");
}

export function startTear(state: EnterNarrativeState): EnterNarrativeState {
  return transitionEnterNarrative(state, "startTear");
}

export function cancelTear(state: EnterNarrativeState): EnterNarrativeState {
  return transitionEnterNarrative(state, "cancelTear");
}

export function completeTear(state: EnterNarrativeState): EnterNarrativeState {
  return transitionEnterNarrative(state, "tearComplete");
}

export function meltDurationMs(reducedMotion: boolean): number {
  return reducedMotion ? 160 : 2200;
}

export function tearDurationMs(reducedMotion: boolean): number {
  return reducedMotion ? 160 : 1180;
}
