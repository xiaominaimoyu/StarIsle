import { useId, useMemo, type ReactNode } from "react";
import "./tear-mask.css";
import type { NarrativeQuality } from "../../lib/narrative/quality";
import {
  CANONICAL_TEAR_SEED,
  createTearGeometry,
  getTearFilterRegion,
  projectTearGeometry,
  type TearGeometry,
} from "../../lib/narrative/tear-geometry";

export interface TearMaskProps {
  readonly geometry?: TearGeometry;
  readonly progress?: number;
  readonly quality?: NarrativeQuality;
  readonly className?: string;
  readonly children?: ReactNode;
}

const VIEWBOX_WIDTH = 1000;
const VIEWBOX_HEIGHT = 240;
const FILTER_PADDING = 18;
const MASK_STROKE_WIDTH = 34;

/** Shared canonical geometry; callers may override it for deterministic fixtures. */
export const DEFAULT_TEAR_GEOMETRY = createTearGeometry({ seed: CANONICAL_TEAR_SEED });

function clampProgress(progress: number): number {
  if (!Number.isFinite(progress)) return 0;
  return Math.min(1, Math.max(0, progress));
}

/** Static SVG tear layer. Pointer, navigation, Canvas, and audio belong to later phases. */
export default function TearMask({
  geometry = DEFAULT_TEAR_GEOMETRY,
  progress = 1,
  quality = "low",
  className,
  children,
}: TearMaskProps) {
  const instanceId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const maskId = `narrative-tear-mask-${instanceId}`;
  const filterId = `narrative-tear-filter-${instanceId}`;
  const projectedGeometry = useMemo(
    () => projectTearGeometry(geometry, VIEWBOX_WIDTH, VIEWBOX_HEIGHT),
    [geometry],
  );
  const path = projectedGeometry.path;
  const filterRegion = getTearFilterRegion(projectedGeometry, FILTER_PADDING);
  const safeProgress = clampProgress(progress);
  const dashOffset = 1 - safeProgress;
  const filterEnabled = quality === "high";
  const classes = ["narrative-tear-mask", className].filter(Boolean).join(" ");

  return (
    <svg
      className={classes}
      viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
      data-quality={quality}
      data-filter-mode={filterEnabled ? "fiber" : "outline"}
      style={{ pointerEvents: "none" }}
    >
      <defs>
        <mask
          id={maskId}
          x="0"
          y="0"
          width={VIEWBOX_WIDTH}
          height={VIEWBOX_HEIGHT}
          maskUnits="userSpaceOnUse"
          maskContentUnits="userSpaceOnUse"
          style={{ maskType: "alpha" }}
        >
          <rect
            x="0"
            y="0"
            width={VIEWBOX_WIDTH}
            height={VIEWBOX_HEIGHT}
            fill="transparent"
          />
          <path
            d={path}
            pathLength="1"
            fill="none"
            stroke="white"
            strokeWidth={MASK_STROKE_WIDTH}
            strokeLinecap="butt"
            strokeLinejoin="round"
            strokeDasharray="1"
            strokeDashoffset={dashOffset}
          />
        </mask>

        {filterEnabled && (
          <filter
            id={filterId}
            x={filterRegion.x}
            y={filterRegion.y}
            width={filterRegion.width}
            height={filterRegion.height}
            filterUnits="userSpaceOnUse"
            primitiveUnits="userSpaceOnUse"
            colorInterpolationFilters="sRGB"
          >
            <feTurbulence
              type="fractalNoise"
              baseFrequency="0.006 0.07"
              numOctaves="1"
              seed={CANONICAL_TEAR_SEED}
              result="fiber-noise"
            />
            <feDisplacementMap
              in="SourceGraphic"
              in2="fiber-noise"
              scale="5"
              xChannelSelector="R"
              yChannelSelector="G"
            />
          </filter>
        )}
      </defs>

      <g
        className="narrative-tear-mask__opening"
        mask={`url(#${maskId})`}
        filter={filterEnabled ? `url(#${filterId})` : undefined}
      >
        {children ?? (
          <path
            d={path}
            pathLength="1"
            fill="none"
            stroke="currentColor"
            strokeWidth="22"
            strokeLinecap="butt"
            strokeLinejoin="round"
          />
        )}
      </g>

      <path
        className="narrative-tear-mask__outline"
        d={path}
        pathLength="1"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="butt"
        strokeLinejoin="round"
        strokeDasharray="1"
        strokeDashoffset={dashOffset}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
