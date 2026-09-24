import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  activateSeal,
  cancelTear,
  completeMelt,
  completeTear,
  meltDurationMs,
  renderedEnterNarrativeState,
  startTear,
  tearDurationMs,
  type EnterNarrativeState,
} from "@/lib/narrative/entry-state";
import {
  lowerNarrativeQuality,
  selectNarrativeQuality,
  type NarrativeQuality,
  type NarrativeQualitySignals,
} from "@/lib/narrative/quality";
import WaxSealSvg from "./WaxSealSvg";
import SealMonogramSvg from "./SealMonogramSvg";
import TearDebrisCanvas, { type DebrisBand } from "./TearDebrisCanvas";
import type { TearFront } from "@/lib/narrative/tear-debris";
import "./narrative-entry.css";

const deskAsset = "/entry/starisle-desk.webp";
const deskPortraitAsset = "/entry/starisle-desk-portrait.webp";
const envelopeAsset = "/entry/starisle-envelope.webp";
const tearThreshold = 0.72;

/** The rip line runs across the envelope's own box, just below the flap tip. */
const ENVELOPE_TEAR_BAND: DebrisBand = Object.freeze({
  x: 0.02,
  y: 0.61,
  width: 0.96,
  height: 0.1,
});

/** Both rip paths start at the flap centre and run outward, so the tear has two fronts. */
const ENVELOPE_TEAR_FRONTS: readonly TearFront[] = Object.freeze([
  Object.freeze({ origin: 0.5, direction: -1 as const }),
  Object.freeze({ origin: 0.5, direction: 1 as const }),
]);

interface NavigatorCapabilities extends Navigator {
  connection?: EventTarget & { saveData?: boolean };
  deviceMemory?: number;
}

interface TearGesture {
  pointerId: number;
  startX: number;
  startY: number;
  width: number;
  height: number;
}

type EntryStyle = CSSProperties & {
  "--tear-progress": number;
  "--tear-direction": number;
  "--tear-lift": number;
};

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

function statusMessage(state: EnterNarrativeState, progress: number): string {
  if (state === "melting") return "封蜡正在融化，即将进入公开页。";
  if (state === "dragging") return `正在撕开信封，已完成 ${Math.round(progress * 100)}%。`;
  if (state === "privateExit") return "信封已经撕开，即将进入私人登录页。";
  if (state === "publicExit") return "封蜡已经融化，即将进入公开页。";
  return "信封尚未开启。";
}

/** Immersive visual entrance only; authentication stays on the existing /login route. */
export default function NarrativeEntry() {
  const [state, setState] = useState<EnterNarrativeState>(renderedEnterNarrativeState);
  const [tearProgress, setTearProgress] = useState(0);
  const [tearDirection, setTearDirection] = useState(1);
  const [tearLift, setTearLift] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [quality, setQuality] = useState<NarrativeQuality>("medium");
  const qualityInitializedRef = useRef(false);
  const wasReducedRef = useRef(false);
  const gestureRef = useRef<TearGesture | null>(null);
  const tearProgressRef = useRef(0);

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
      window.dispatchEvent(
        new CustomEvent("narrative:public-enter", {
          bubbles: true,
          detail: { at: Date.now(), method: "seal" },
        }),
      );
    }, meltDurationMs(reducedMotion));

    return () => window.clearTimeout(completionTimer);
  }, [reducedMotion, state]);

  useEffect(() => {
    if (state !== "privateExit") return;

    const completionTimer = window.setTimeout(() => {
      window.dispatchEvent(
        new CustomEvent("narrative:private-enter", {
          bubbles: true,
          detail: { at: Date.now(), method: "tear" },
        }),
      );
    }, tearDurationMs(reducedMotion));

    return () => window.clearTimeout(completionTimer);
  }, [reducedMotion, state]);

  const beginSealMelt = (event: ReactMouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    setState((currentState) => activateSeal(currentState));
  };

  const handleSealKeyDown = (event: ReactKeyboardEvent<HTMLAnchorElement>) => {
    if (event.key !== " ") return;
    event.preventDefault();
    setState((currentState) => activateSeal(currentState));
  };

  const beginTear = (event: ReactPointerEvent<HTMLAnchorElement>) => {
    if (state !== "sealed" || event.button !== 0) return;
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    gestureRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      width: Math.max(rect.width, 1),
      height: Math.max(rect.height, 1),
    };
    tearProgressRef.current = 0;
    setTearProgress(0);
    setTearDirection(1);
    setTearLift(0);
    setState((currentState) => startTear(currentState));
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const moveTear = (event: ReactPointerEvent<HTMLAnchorElement>) => {
    const gesture = gestureRef.current;
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    event.preventDefault();
    const deltaX = event.clientX - gesture.startX;
    const deltaY = event.clientY - gesture.startY;
    const horizontal = Math.abs(deltaX);
    const vertical = Math.abs(deltaY) * 0.28;
    const progress = Math.min(1, Math.hypot(horizontal, vertical) / (gesture.width * 0.52));
    tearProgressRef.current = progress;
    setTearProgress(progress);
    if (Math.abs(deltaX) > 2) setTearDirection(deltaX < 0 ? -1 : 1);
    setTearLift(Math.max(-1, Math.min(1, -deltaY / (gesture.height * 0.42))));
  };

  const endTear = (event: ReactPointerEvent<HTMLAnchorElement>) => {
    const gesture = gestureRef.current;
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    event.preventDefault();
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    gestureRef.current = null;

    if (tearProgressRef.current >= tearThreshold) {
      tearProgressRef.current = 1;
      setTearProgress(1);
      setState((currentState) => completeTear(currentState));
      return;
    }

    tearProgressRef.current = 0;
    setTearProgress(0);
    setTearDirection(1);
    setTearLift(0);
    setState((currentState) => cancelTear(currentState));
  };

  const abortTear = (event: ReactPointerEvent<HTMLAnchorElement>) => {
    const gesture = gestureRef.current;
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    gestureRef.current = null;
    tearProgressRef.current = 0;
    setTearProgress(0);
    setTearDirection(1);
    setTearLift(0);
    setState((currentState) => cancelTear(currentState));
  };

  const activatePrivateFallback = (
    event: ReactMouseEvent<HTMLAnchorElement> | ReactKeyboardEvent<HTMLAnchorElement>,
  ) => {
    event.preventDefault();
    if (state !== "sealed") return;
    tearProgressRef.current = 1;
    setTearProgress(1);
    setTearDirection(1);
    setTearLift(0.35);
    setState((currentState) => completeTear(startTear(currentState)));
  };

  const handleTearClick = (event: ReactMouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    if (event.detail === 0) activatePrivateFallback(event);
  };

  const handleTearKeyDown = (event: ReactKeyboardEvent<HTMLAnchorElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    activatePrivateFallback(event);
  };

  const entryStyle: EntryStyle = {
    "--tear-progress": tearProgress,
    "--tear-direction": tearDirection,
    "--tear-lift": tearLift,
  };
  const envelopeImageProps = {
    src: envelopeAsset,
    alt: "",
    width: 1200,
    height: 810,
    draggable: false,
  } as const;

  return (
    <section
      className="narrative-entry"
      data-state={state}
      data-quality={quality}
      style={entryStyle}
      aria-labelledby="narrative-entry-title"
      aria-describedby="narrative-entry-instructions"
    >
      <h1 id="narrative-entry-title" className="sr-only">星屿入口信封</h1>
      <p id="narrative-entry-instructions" className="sr-only">
        点击中央封蜡进入公开页；在信封翻盖区域拖动撕开，或聚焦后按回车键，进入私人登录页。
      </p>

      <div className="narrative-entry__stage">
        <picture className="narrative-entry__desk-frame">
          <source media="(max-aspect-ratio: 7 / 5)" srcSet={deskPortraitAsset} />
          <img
            src={deskAsset}
            alt=""
            width={1600}
            height={914}
            draggable={false}
            className="narrative-entry__desk"
          />
        </picture>
        <div className="narrative-entry__envelope" aria-hidden="true">
          <span className="narrative-entry__glow" />
          <div className="narrative-entry__image-stack">
            <img {...envelopeImageProps} className="narrative-entry__image narrative-entry__image--sealed" fetchPriority="high" />
            <img {...envelopeImageProps} className="narrative-entry__image narrative-entry__image--fragment narrative-entry__image--upper" />
            <img {...envelopeImageProps} className="narrative-entry__image narrative-entry__image--fragment narrative-entry__image--lower" />
          </div>

        <svg
          className="narrative-entry__rip"
          viewBox="0 0 1000 100"
          preserveAspectRatio="none"
          aria-hidden="true"
          focusable="false"
        >
          <defs>
            <filter id="narrative-tear-roughen" x="-4%" y="-35%" width="108%" height="170%">
              <feTurbulence
                type="fractalNoise"
                baseFrequency="0.012 0.16"
                numOctaves="1"
                seed="29"
                result="tearNoise"
              />
              <feDisplacementMap
                in="SourceGraphic"
                in2="tearNoise"
                scale="3.2"
                xChannelSelector="R"
                yChannelSelector="G"
              />
            </filter>
          </defs>
          <path
            className="narrative-entry__rip-path narrative-entry__rip-void narrative-entry__rip-path--left"
            pathLength="1"
            d="M500 51 487 48 474 54 462 49 449 52 437 46 424 53 411 50 398 55 386 48 373 51 360 45 347 53 334 49 321 56 307 47 294 52 281 46 268 54 254 49 240 55 226 47 212 53 198 45 184 54 170 48 155 56 141 47 126 52 111 44 96 54 80 49 64 57 48 46 32 52 16 47 0 52"
          />
          <path
            className="narrative-entry__rip-path narrative-entry__rip-void narrative-entry__rip-path--right"
            pathLength="1"
            d="M500 51 514 55 527 48 540 53 553 46 566 52 580 49 594 57 608 47 622 54 636 48 650 55 665 45 680 53 695 49 710 56 725 47 740 52 755 46 770 55 786 48 802 57 818 46 834 53 850 49 866 56 883 45 900 54 917 48 934 55 951 46 968 53 984 48 1000 51"
          />
          <path
            className="narrative-entry__rip-path narrative-entry__rip-paper narrative-entry__rip-path--left"
            pathLength="1"
            d="M500 51 487 48 474 54 462 49 449 52 437 46 424 53 411 50 398 55 386 48 373 51 360 45 347 53 334 49 321 56 307 47 294 52 281 46 268 54 254 49 240 55 226 47 212 53 198 45 184 54 170 48 155 56 141 47 126 52 111 44 96 54 80 49 64 57 48 46 32 52 16 47 0 52"
          />
          <path
            className="narrative-entry__rip-path narrative-entry__rip-paper narrative-entry__rip-path--right"
            pathLength="1"
            d="M500 51 514 55 527 48 540 53 553 46 566 52 580 49 594 57 608 47 622 54 636 48 650 55 665 45 680 53 695 49 710 56 725 47 740 52 755 46 770 55 786 48 802 57 818 46 834 53 850 49 866 56 883 45 900 54 917 48 934 55 951 46 968 53 984 48 1000 51"
          />
          <path
            className="narrative-entry__rip-path narrative-entry__rip-fiber narrative-entry__rip-path--left"
            pathLength="1"
            d="M500 48 487 45 474 51 462 46 449 49 437 43 424 50 411 47 398 52 386 45 373 48 360 42 347 50 334 46 321 53 307 44 294 49 281 43 268 51 254 46 240 52 226 44 212 50 198 42 184 51 170 45 155 53 141 44 126 49 111 41 96 51 80 46 64 54 48 43 32 49 16 44 0 49"
          />
          <path
            className="narrative-entry__rip-path narrative-entry__rip-fiber narrative-entry__rip-path--right"
            pathLength="1"
            d="M500 48 514 52 527 45 540 50 553 43 566 49 580 46 594 54 608 44 622 51 636 45 650 52 665 42 680 50 695 46 710 53 725 44 740 49 755 43 770 52 786 45 802 54 818 43 834 50 850 46 866 53 883 42 900 51 917 45 934 52 951 43 968 50 984 45 1000 48"
          />
        </svg>

        <TearDebrisCanvas
          progress={tearProgress}
          quality={quality}
          band={ENVELOPE_TEAR_BAND}
          fronts={ENVELOPE_TEAR_FRONTS}
        />

        <a
          href="/login"
          className="narrative-entry__tear-zone"
          aria-label="撕开信封，进入私人登录页"
          aria-describedby="narrative-entry-instructions"
          onClick={handleTearClick}
          onKeyDown={handleTearKeyDown}
          onPointerDown={beginTear}
          onPointerMove={moveTear}
          onPointerUp={endTear}
          onPointerCancel={abortTear}
          onDragStart={(event) => event.preventDefault()}
        >
          <span className="sr-only">撕开信封，进入私人登录页</span>
        </a>

        <a
          href="/"
          className="narrative-entry__seal"
          aria-label="融化封蜡，进入公开页"
          aria-describedby="narrative-entry-instructions"
          aria-disabled={state !== "sealed"}
          onClick={beginSealMelt}
          onKeyDown={handleSealKeyDown}
          onDragStart={(event) => event.preventDefault()}
        >
          <SealMonogramSvg className="narrative-entry__seal-face" variant="seal" />
          <WaxSealSvg />
          <span className="sr-only">融化封蜡，进入公开页</span>
        </a>
        </div>
      </div>

      <p className="sr-only" aria-live="polite">{statusMessage(state, tearProgress)}</p>
    </section>
  );
}
