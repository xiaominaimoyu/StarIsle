/**
 * Presentation-only state for the /enter route.
 *
 * This deliberately has no authentication, navigation, storage, or API
 * concerns. A full-document navigation destroys this state with the page.
 */
export type EnterNarrativeState = "sealed" | "melting" | "loginReveal";

export const initialEnterNarrativeState: EnterNarrativeState = "sealed";

export function activateSeal(state: EnterNarrativeState): EnterNarrativeState {
  return state === "sealed" ? "melting" : state;
}

export function completeMelt(state: EnterNarrativeState): EnterNarrativeState {
  return state === "melting" ? "loginReveal" : state;
}

export function meltDurationMs(reducedMotion: boolean): number {
  return reducedMotion ? 140 : 2600;
}
