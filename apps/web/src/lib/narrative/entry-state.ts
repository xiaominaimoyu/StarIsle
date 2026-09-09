/**
 * Presentation-only state for the /enter route.
 *
 * This deliberately has no authentication, navigation, storage, or API
 * concerns. A full-document navigation destroys this state with the page.
 */
export type EnterNarrativeState = "idle" | "sealed" | "melting" | "loginReveal" | "isle";
export type EnterNarrativeEvent =
  | "render"
  | "activateSeal"
  | "meltComplete"
  | "loginFailure"
  | "loginSuccess";

export const initialEnterNarrativeState: EnterNarrativeState = "idle";

export function transitionEnterNarrative(
  state: EnterNarrativeState,
  event: EnterNarrativeEvent,
): EnterNarrativeState {
  if (state === "idle" && event === "render") return "sealed";
  if (state === "sealed" && event === "activateSeal") return "melting";
  if (state === "melting" && event === "meltComplete") return "loginReveal";
  if (state === "loginReveal" && event === "loginSuccess") return "isle";
  return state;
}

/** React SSR 已经越过概念上的 render 边界，首个可操作状态必须是 sealed。 */
export function renderedEnterNarrativeState(): EnterNarrativeState {
  return transitionEnterNarrative(initialEnterNarrativeState, "render");
}

export function activateSeal(state: EnterNarrativeState): EnterNarrativeState {
  return transitionEnterNarrative(state, "activateSeal");
}

export function completeMelt(state: EnterNarrativeState): EnterNarrativeState {
  return transitionEnterNarrative(state, "meltComplete");
}

export function reportLoginFailure(state: EnterNarrativeState): EnterNarrativeState {
  return transitionEnterNarrative(state, "loginFailure");
}

export function acknowledgeLoginSuccess(state: EnterNarrativeState): EnterNarrativeState {
  return transitionEnterNarrative(state, "loginSuccess");
}

export function meltDurationMs(reducedMotion: boolean): number {
  return reducedMotion ? 140 : 2600;
}
