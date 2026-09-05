import { useEffect, useState } from "react";
import {
  activateSeal,
  completeMelt,
  initialEnterNarrativeState,
  meltDurationMs,
  type EnterNarrativeState,
} from "@/lib/narrative/entry-state";
import "./narrative-entry.css";

function statusMessage(state: EnterNarrativeState): string {
  if (state === "melting") return "封印正在消融。";
  if (state === "loginReveal") return "信已开启。普通登录链接仍可用。";
  return "";
}

/** Visual enhancement for /enter only; it owns no authentication state. */
export default function NarrativeEntry() {
  const [state, setState] = useState<EnterNarrativeState>(initialEnterNarrativeState);
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updateMotionPreference = () => setReducedMotion(mediaQuery.matches);

    updateMotionPreference();
    mediaQuery.addEventListener("change", updateMotionPreference);
    return () => mediaQuery.removeEventListener("change", updateMotionPreference);
  }, []);

  useEffect(() => {
    if (state !== "melting") return;

    const completionTimer = window.setTimeout(() => {
      setState((currentState) => completeMelt(currentState));
    }, meltDurationMs(reducedMotion));

    return () => window.clearTimeout(completionTimer);
  }, [reducedMotion, state]);

  return (
    <section className="narrative-entry" data-state={state} aria-labelledby="narrative-seal-title">
      <div className="narrative-entry__envelope" aria-hidden="true">
        <div className="narrative-entry__letter" />
        <div className="narrative-entry__flap" />
      </div>
      <div className="narrative-entry__seal-wrap">
        <button
          type="button"
          className="narrative-entry__seal"
          onClick={() => setState((currentState) => activateSeal(currentState))}
          disabled={state !== "sealed"}
          aria-describedby="narrative-seal-description"
        >
          <svg viewBox="0 0 180 180" aria-hidden="true" focusable="false">
            <defs>
              <filter
                id="narrative-wax-melt"
                x="20"
                y="18"
                width="140"
                height="145"
                filterUnits="userSpaceOnUse"
                primitiveUnits="userSpaceOnUse"
              >
                <feTurbulence type="fractalNoise" baseFrequency="0.018" numOctaves="2" seed="37" result="grain" />
                <feDisplacementMap in="SourceGraphic" in2="grain" scale="3" xChannelSelector="R" yChannelSelector="G" />
              </filter>
            </defs>
            <g className="narrative-entry__wax" filter="url(#narrative-wax-melt)">
              <circle className="narrative-entry__wax-base" cx="90" cy="84" r="58" />
              <circle className="narrative-entry__wax-rim" cx="90" cy="84" r="46" />
              <path className="narrative-entry__wax-imprint" d="M90 50 99 75l25 9-25 9-9 25-9-25-25-9 25-9z" />
            </g>
            <path className="narrative-entry__wax-residue" d="M55 122c14 8 24 13 35 13s22-5 35-13l-8 20H63z" />
          </svg>
          <span>开启封印</span>
        </button>
      </div>
      <h2 id="narrative-seal-title" className="sr-only">信封封印</h2>
      <p id="narrative-seal-description" className="sr-only">按下后展示信件，不会执行登录或跳转。</p>
      <p className="sr-only" aria-live="polite">{statusMessage(state)}</p>
      {state === "loginReveal" ? (
        <div className="narrative-entry__revealed" aria-hidden="true">信已开启</div>
      ) : null}
    </section>
  );
}
