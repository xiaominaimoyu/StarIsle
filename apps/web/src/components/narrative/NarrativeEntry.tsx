import { useEffect, useRef, useState } from "react";
import {
  activateSeal,
  completeMelt,
  meltDurationMs,
  renderedEnterNarrativeState,
  type EnterNarrativeState,
} from "@/lib/narrative/entry-state";
import {
  lowerNarrativeQuality,
  selectNarrativeQuality,
  type NarrativeQuality,
  type NarrativeQualitySignals,
} from "@/lib/narrative/quality";
import LoginForm from "../auth/LoginForm";
import WaxSealSvg from "./WaxSealSvg";
import "./narrative-entry.css";

function statusMessage(state: EnterNarrativeState): string {
  if (state === "melting") return "封印正在消融。";
  if (state === "loginReveal") return "封印已打开，登录信件已经显示。";
  return "";
}

interface NavigatorCapabilities extends Navigator {
  connection?: EventTarget & { saveData?: boolean };
  deviceMemory?: number;
}

function qualitySignals(reducedMotion: boolean, measuredFps?: number): NarrativeQualitySignals {
  const capabilities = navigator as NavigatorCapabilities;
  return {
    reducedMotion,
    saveData: capabilities.connection?.saveData === true,
    viewportWidth: window.innerWidth,
    deviceMemory: capabilities.deviceMemory,
    measuredFps,
  };
}

/** Visual enhancement for /enter only; it owns no authentication state. */
export default function NarrativeEntry() {
  const [state, setState] = useState<EnterNarrativeState>(renderedEnterNarrativeState);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [quality, setQuality] = useState<NarrativeQuality>("medium");
  const qualityInitializedRef = useRef(false);
  const wasReducedRef = useRef(false);
  const letterTitleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const capabilities = navigator as NavigatorCapabilities;
    const connection = capabilities.connection;

    const updateQuality = (allowPreferenceUpgrade = false) => {
      const isReduced = mediaQuery.matches;
      const candidate = selectNarrativeQuality(qualitySignals(isReduced));
      setReducedMotion(isReduced);
      setQuality((currentQuality) => {
        if (!qualityInitializedRef.current || allowPreferenceUpgrade) return candidate;
        return lowerNarrativeQuality(currentQuality, candidate);
      });
      qualityInitializedRef.current = true;
      wasReducedRef.current = isReduced;
    };

    const updateMotionPreference = () => {
      const preferenceWasRemoved = wasReducedRef.current && !mediaQuery.matches;
      updateQuality(preferenceWasRemoved);
    };
    const lowerForCapabilityChange = () => updateQuality(false);

    updateMotionPreference();
    mediaQuery.addEventListener("change", updateMotionPreference);
    window.addEventListener("resize", lowerForCapabilityChange, { passive: true });
    connection?.addEventListener("change", lowerForCapabilityChange);
    return () => {
      mediaQuery.removeEventListener("change", updateMotionPreference);
      window.removeEventListener("resize", lowerForCapabilityChange);
      connection?.removeEventListener("change", lowerForCapabilityChange);
    };
  }, []);

  useEffect(() => {
    if (reducedMotion) return;

    let animationFrame = 0;
    let frameCount = 0;
    let sampleStartedAt: number | undefined;
    const sampleFrameRate = (timestamp: number) => {
      if (sampleStartedAt === undefined) sampleStartedAt = timestamp;
      frameCount += 1;
      const elapsed = timestamp - sampleStartedAt;
      if (elapsed < 320) {
        animationFrame = window.requestAnimationFrame(sampleFrameRate);
        return;
      }
      const measuredFps = elapsed > 0 ? (frameCount - 1) * 1000 / elapsed : 60;
      const candidate = selectNarrativeQuality(qualitySignals(false, measuredFps));
      setQuality((currentQuality) => lowerNarrativeQuality(currentQuality, candidate));
    };

    animationFrame = window.requestAnimationFrame(sampleFrameRate);
    return () => window.cancelAnimationFrame(animationFrame);
  }, [reducedMotion]);

  useEffect(() => {
    if (state !== "melting") return;

    const completionTimer = window.setTimeout(() => {
      setState((currentState) => completeMelt(currentState));
    }, meltDurationMs(reducedMotion));

    return () => window.clearTimeout(completionTimer);
  }, [reducedMotion, state]);

  useEffect(() => {
    if (state !== "loginReveal") return;

    const focusFrame = window.requestAnimationFrame(() => letterTitleRef.current?.focus());
    return () => window.cancelAnimationFrame(focusFrame);
  }, [state]);

  const revealed = state === "loginReveal" || state === "isle";

  return (
    <section
      className="narrative-entry"
      data-state={state}
      data-quality={quality}
      aria-labelledby="narrative-seal-title"
    >
      <div className="narrative-entry__scene">
        <div className="narrative-entry__envelope" aria-hidden="true">
          <div className="narrative-entry__paper-fibers" />
          <div className="narrative-entry__letter" />
          <div className="narrative-entry__flap" />
        </div>
        <div className="narrative-entry__seal-wrap">
          <button
            type="button"
            className="narrative-entry__seal"
            onClick={() => setState((currentState) => activateSeal(currentState))}
            disabled={state !== "sealed"}
            aria-controls="narrative-letter"
            aria-describedby="narrative-seal-description"
            aria-expanded={revealed}
          >
            <WaxSealSvg />
            <span>开启封印</span>
          </button>
        </div>
      </div>
      <h2 id="narrative-seal-title" className="sr-only">信封封印</h2>
      <p id="narrative-seal-description" className="sr-only">按下后展示信件，不会执行登录或跳转。</p>
      <p className="sr-only" aria-live="polite">{statusMessage(state)}</p>
      <article
        id="narrative-letter"
        className="narrative-entry__revealed"
        hidden={!revealed}
      >
        <h2 ref={letterTitleRef} tabIndex={-1}>信已开启</h2>
        <p>海风已经吹开信页。你可以直接在信中登录，也可以改用普通登录页或返回公开星图。</p>
        {revealed ? (
          <section
            className="narrative-entry__login"
            aria-labelledby="narrative-login-title"
            aria-describedby="narrative-login-description"
          >
            <h3 id="narrative-login-title">进入你的岛屿</h3>
            <p id="narrative-login-description">这里复用现有安全登录流程；成功后将进入控制台并重新校验会话。</p>
            <LoginForm />
          </section>
        ) : null}
        <nav aria-label="信件中的继续前往">
          <a href="/login">普通登录</a>
          <a href="/">前往星</a>
        </nav>
      </article>
    </section>
  );
}
