import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  beginTearGesture,
  cancelTearGesture,
  createIdleTearGesture,
  finishTearGesture,
  moveTearGesture,
  type TearGestureState,
  type TearPointerSample,
} from "../../lib/narrative/gesture-controller";
import TearMask from "./TearMask";
import "./tear-portal-link.css";

type TearPortalPhase = "isleIdle" | "tearing" | "starCommit";
type TearCancelReason =
  | "capture-failed"
  | "escape"
  | "hidden"
  | "invalid-sample"
  | "lost-capture"
  | "motion-change"
  | "pointercancel"
  | "resize"
  | "secondary-pointer"
  | "vertical-intent";

interface TearPortalView {
  readonly phase: TearPortalPhase;
  readonly progress: number;
}

interface PointerSampleLike {
  readonly pointerId: number;
  readonly clientX: number;
  readonly clientY: number;
  readonly timeStamp: number;
  readonly isPrimary: boolean;
}

const IDLE_VIEW: TearPortalView = Object.freeze({ phase: "isleIdle", progress: 0 });
const POINTER_CLICK_SUPPRESSION_MS = 500;
const NORMAL_NAVIGATION_DELAY_MS = 220;
const REDUCED_NAVIGATION_DELAY_MS = 140;

function toPointerSample(event: PointerSampleLike): TearPointerSample {
  return {
    pointerId: event.pointerId,
    clientX: event.clientX,
    clientY: event.clientY,
    timeStamp: event.timeStamp,
    isPrimary: event.isPrimary,
  };
}

function cancellationAnnouncement(reason: TearCancelReason): string {
  if (reason === "vertical-intent") return "已保留纵向滚动，未前往星。";
  if (reason === "escape") return "撕裂已取消，仍在控制台。";
  return "撕裂未完成，仍在控制台。";
}

function StarBackdropSurface() {
  return (
    <>
      <rect width="1000" height="240" fill="var(--tear-star-deep)" />
      <path d="M0 198 C180 126 286 220 470 148 S790 88 1000 170 V240 H0Z" fill="#172641" opacity="0.72" />
      <path d="M0 54 C196 18 328 112 508 62 S812 14 1000 82" fill="none" stroke="var(--tear-star-glow)" strokeWidth="2" opacity="0.2" />
      <path d="M0 192 C184 128 302 214 476 148 S810 94 1000 164" fill="none" stroke="var(--tear-star-light)" strokeWidth="2" opacity="0.18" />
    </>
  );
}

/** Console-only enhancement. Authentication remains owned by ConsoleSessionGate. */
export default function TearPortalLink() {
  const descriptionId = `tear-portal-description-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const panelRef = useRef<HTMLElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const gestureRef = useRef<TearGestureState>(createIdleTearGesture());
  const animationFrameRef = useRef(0);
  const navigationTimerRef = useRef<number | null>(null);
  const navigationScheduledRef = useRef(false);
  const suppressPointerClickUntilRef = useRef(0);
  const blockedSecondaryPointerRef = useRef<number | null>(null);
  const reducedMotionRef = useRef(false);
  const [view, setView] = useState<TearPortalView>(IDLE_VIEW);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  const cancelPendingFrame = useCallback(() => {
    if (!animationFrameRef.current) return;
    window.cancelAnimationFrame(animationFrameRef.current);
    animationFrameRef.current = 0;
  }, []);

  const releasePointer = useCallback((pointerId: number | null) => {
    const button = buttonRef.current;
    if (pointerId === null || !button) return;
    try {
      if (button.hasPointerCapture(pointerId)) button.releasePointerCapture(pointerId);
    } catch {
      // Capture may already have been released by the browser.
    }
  }, []);

  const cancelActiveGesture = useCallback((
    reason: TearCancelReason,
    restoreFocus = false,
  ) => {
    const active = gestureRef.current;
    if (active.phase !== "tracking") return false;

    cancelPendingFrame();
    gestureRef.current = cancelTearGesture(active);
    releasePointer(active.pointerId);
    // A cancelled pointer sequence must never fall through to the browser's
    // synthetic click, even when cancellation happened before axis lock.
    suppressPointerClickUntilRef.current = window.performance.now()
      + POINTER_CLICK_SUPPRESSION_MS;
    setView(IDLE_VIEW);
    setAnnouncement(cancellationAnnouncement(reason));
    if (restoreFocus) buttonRef.current?.focus({ preventScroll: true });
    return true;
  }, [cancelPendingFrame, releasePointer]);

  const scheduleProgressRender = useCallback(() => {
    if (animationFrameRef.current) return;
    animationFrameRef.current = window.requestAnimationFrame(() => {
      animationFrameRef.current = 0;
      const active = gestureRef.current;
      if (active.phase !== "tracking" || active.axis !== "horizontal") return;
      setView({ phase: "tearing", progress: active.progress });
    });
  }, []);

  const commitNavigation = useCallback((state?: TearGestureState) => {
    if (navigationScheduledRef.current) return;
    navigationScheduledRef.current = true;
    cancelPendingFrame();
    if (state) gestureRef.current = state;
    setView({ phase: "starCommit", progress: 1 });
    setAnnouncement("撕裂完成，即将前往星。");
    const delay = reducedMotionRef.current
      ? REDUCED_NAVIGATION_DELAY_MS
      : NORMAL_NAVIGATION_DELAY_MS;
    navigationTimerRef.current = window.setTimeout(() => {
      navigationTimerRef.current = null;
      window.location.assign("/");
    }, delay);
  }, [cancelPendingFrame]);

  useEffect(() => {
    const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updateMotionPreference = () => {
      const isReduced = motionQuery.matches;
      reducedMotionRef.current = isReduced;
      setReducedMotion(isReduced);
      if (isReduced) cancelActiveGesture("motion-change");
    };
    const cancelForEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (cancelActiveGesture("escape", true)) event.preventDefault();
    };
    const cancelForResize = () => cancelActiveGesture("resize");
    const cancelWhenHidden = () => {
      if (document.visibilityState === "hidden") cancelActiveGesture("hidden");
    };
    const cancelForSecondPointer = (event: PointerEvent) => {
      const active = gestureRef.current;
      if (active.phase !== "tracking" || event.pointerId === active.pointerId) return;
      // Cancel the tear without cancelling the browser's native pinch-zoom.
      blockedSecondaryPointerRef.current = event.pointerId;
      cancelActiveGesture("secondary-pointer");
      queueMicrotask(() => {
        if (blockedSecondaryPointerRef.current === event.pointerId) {
          blockedSecondaryPointerRef.current = null;
        }
      });
    };

    updateMotionPreference();
    motionQuery.addEventListener("change", updateMotionPreference);
    window.addEventListener("keydown", cancelForEscape);
    window.addEventListener("resize", cancelForResize, { passive: true });
    window.addEventListener("pointerdown", cancelForSecondPointer, true);
    document.addEventListener("visibilitychange", cancelWhenHidden);
    return () => {
      motionQuery.removeEventListener("change", updateMotionPreference);
      window.removeEventListener("keydown", cancelForEscape);
      window.removeEventListener("resize", cancelForResize);
      window.removeEventListener("pointerdown", cancelForSecondPointer, true);
      document.removeEventListener("visibilitychange", cancelWhenHidden);
      cancelPendingFrame();
      if (navigationTimerRef.current !== null) {
        window.clearTimeout(navigationTimerRef.current);
        navigationTimerRef.current = null;
      }
      const active = gestureRef.current;
      if (active.phase === "tracking") releasePointer(active.pointerId);
      gestureRef.current = createIdleTearGesture();
    };
  }, [cancelActiveGesture, cancelPendingFrame, releasePointer]);

  const handlePointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (blockedSecondaryPointerRef.current === event.pointerId) return;
    const active = gestureRef.current;
    if (active.phase === "tracking") {
      if (event.pointerId !== active.pointerId) cancelActiveGesture("secondary-pointer");
      return;
    }
    if (reducedMotionRef.current || navigationScheduledRef.current) return;
    if (!event.isPrimary || event.button !== 0) return;

    const panelWidth = panelRef.current?.getBoundingClientRect().width ?? 0;
    const handleWidth = event.currentTarget.getBoundingClientRect().width;
    const travel = Math.max(120, panelWidth - handleWidth - 8);
    const started = beginTearGesture(
      active,
      toPointerSample(event),
      travel,
    );
    if (started.outcome !== "started") return;

    gestureRef.current = started.state;
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      cancelActiveGesture("capture-failed");
      return;
    }
    setAnnouncement("");
    setView({ phase: "tearing", progress: 0 });
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const active = gestureRef.current;
    if (active.phase !== "tracking" || event.pointerId !== active.pointerId) return;
    const moved = moveTearGesture(active, toPointerSample(event));
    if (moved.phase !== "tracking") {
      cancelActiveGesture("invalid-sample");
      return;
    }
    gestureRef.current = moved;
    if (moved.axis === "vertical") {
      cancelActiveGesture("vertical-intent");
      return;
    }
    if (moved.axis === "horizontal") {
      event.preventDefault();
      scheduleProgressRender();
    }
  };

  const handlePointerUp = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const active = gestureRef.current;
    const result = finishTearGesture(active, toPointerSample(event));
    if (result.outcome === "ignored") return;

    cancelPendingFrame();
    gestureRef.current = result.state;
    releasePointer(active.pointerId);
    if (result.outcome === "commit") {
      suppressPointerClickUntilRef.current = window.performance.now()
        + POINTER_CLICK_SUPPRESSION_MS;
      commitNavigation(result.state);
      return;
    }
    if (result.outcome === "rollback") {
      suppressPointerClickUntilRef.current = window.performance.now()
        + POINTER_CLICK_SUPPRESSION_MS;
      setAnnouncement("撕裂未达到开启位置，已恢复原状。");
      buttonRef.current?.focus({ preventScroll: true });
    }
    setView(IDLE_VIEW);
  };

  const handlePointerCancellation = (
    event: ReactPointerEvent<HTMLButtonElement>,
    reason: "pointercancel" | "lost-capture",
  ) => {
    if (event.pointerId !== gestureRef.current.pointerId) return;
    cancelActiveGesture(reason);
  };

  const handleClick = (event: ReactMouseEvent<HTMLButtonElement>) => {
    if (
      event.detail > 0
      && window.performance.now() <= suppressPointerClickUntilRef.current
    ) {
      suppressPointerClickUntilRef.current = 0;
      event.preventDefault();
      return;
    }
    commitNavigation();
  };

  const visualStyle = {
    "--tear-progress": view.progress,
    "--tear-shift-up": `${-44 * view.progress}px`,
    "--tear-shift-down": `${44 * view.progress}px`,
    "--tear-rotate-top": `${-1.2 * view.progress}deg`,
    "--tear-rotate-bottom": `${1.2 * view.progress}deg`,
    "--tear-handle-shift": `${12 * view.progress}px`,
  } as CSSProperties;

  return (
    <aside
      ref={panelRef}
      className="tear-portal-link no-print"
      data-phase={view.phase}
      data-motion={reducedMotion ? "reduced" : "full"}
      style={visualStyle}
      aria-label="返回公开门户"
    >
      <div className="tear-portal-link__viewport" aria-hidden="true">
        <div className="tear-portal-link__star-backdrop" />
        <div className="tear-portal-link__paper tear-portal-link__paper--top" />
        <div className="tear-portal-link__paper tear-portal-link__paper--bottom" />
        <TearMask progress={view.progress} quality={reducedMotion ? "reduced" : "low"}>
          <StarBackdropSurface />
        </TearMask>
      </div>

      <button
        ref={buttonRef}
        type="button"
        className="tear-portal-link__handle"
        aria-describedby={descriptionId}
        aria-label="向右撕开纸页前往星"
        disabled={view.phase === "starCommit"}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={(event) => handlePointerCancellation(event, "pointercancel")}
        onLostPointerCapture={(event) => handlePointerCancellation(event, "lost-capture")}
        onClick={handleClick}
        onDragStart={(event) => event.preventDefault()}
      >
        <span aria-hidden="true">星门</span>
        <span className="sr-only">撕开前往星</span>
      </button>

      <div className="tear-portal-link__copy">
        <strong aria-hidden="true">向右撕开</strong>
        <a href="/">普通前往星</a>
      </div>
      <p id={descriptionId} className="sr-only">
        按下后向右拖动；也可直接点击、使用键盘，或选择普通前往星链接。
      </p>
      <p className="sr-only" aria-live="polite">{announcement}</p>
    </aside>
  );
}
