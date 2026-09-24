import { useEffect, useMemo, useRef } from "react";
import type { NarrativeQuality } from "@/lib/narrative/quality";
import {
  createDebrisField,
  debrisAlpha,
  emitDebris,
  stepDebris,
  DEBRIS_TONES,
  type DebrisField,
  type TearFront,
} from "@/lib/narrative/tear-debris";
import {
  CANONICAL_TEAR_SEED,
  createTearGeometry,
  projectTearGeometry,
  type ProjectedTearGeometry,
  type TearGeometry,
} from "@/lib/narrative/tear-geometry";
import "./tear-debris.css";

/** Where the tear line sits inside the canvas box, in fractions. */
export interface DebrisBand {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface TearDebrisCanvasProps {
  readonly progress: number;
  readonly quality: NarrativeQuality;
  readonly band?: DebrisBand;
  readonly fronts?: readonly TearFront[];
  readonly seed?: number;
  readonly className?: string;
}

interface DebrisSurface {
  readonly width: number;
  readonly height: number;
  readonly offsetX: number;
  readonly offsetY: number;
  readonly geometry: ProjectedTearGeometry;
}

const FULL_BAND: DebrisBand = Object.freeze({ x: 0, y: 0, width: 1, height: 1 });
const SINGLE_FRONT: readonly TearFront[] = Object.freeze([
  Object.freeze({ origin: 0, direction: 1 as const }),
]);
const MAX_PIXEL_RATIO = 2;
const FLECK_OPACITY = 0.86;

function pixelRatio(): number {
  return Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
}

function toneFill(tone: number, alpha: number): string {
  const colour = DEBRIS_TONES[tone] ?? DEBRIS_TONES[0];
  return `rgb(${colour.r} ${colour.g} ${colour.b} / ${(alpha * FLECK_OPACITY).toFixed(3)})`;
}

function measureSurface(
  canvas: HTMLCanvasElement,
  geometry: TearGeometry,
  band: DebrisBand,
  cached: DebrisSurface | null,
): DebrisSurface | null {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (width < 2 || height < 2) return null;
  if (cached && cached.width === width && cached.height === height) return cached;

  const ratio = pixelRatio();
  const backingWidth = Math.round(width * ratio);
  const backingHeight = Math.round(height * ratio);
  if (canvas.width !== backingWidth || canvas.height !== backingHeight) {
    canvas.width = backingWidth;
    canvas.height = backingHeight;
  }

  const offsetX = band.x * width;
  const offsetY = band.y * height;
  return {
    width,
    height,
    offsetX,
    offsetY,
    geometry: projectTearGeometry(
      geometry,
      Math.max(1, band.width * width),
      Math.max(1, band.height * height),
    ),
  };
}

/** Only the cull box is band-local, so flecks stay alive while falling past the tear. */
function cullBounds(surface: DebrisSurface) {
  return {
    width: surface.width - surface.offsetX,
    height: surface.height - surface.offsetY,
  };
}

function paint(
  canvas: HTMLCanvasElement,
  context: CanvasRenderingContext2D,
  field: DebrisField,
  surface: DebrisSurface,
): void {
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, canvas.width, canvas.height);

  const ratio = pixelRatio();
  context.setTransform(ratio, 0, 0, ratio, surface.offsetX * ratio, surface.offsetY * ratio);
  for (const fleck of field.flecks) {
    const alpha = debrisAlpha(fleck);
    if (alpha <= 0) continue;

    context.save();
    context.translate(fleck.x, fleck.y);
    context.rotate(fleck.angle);
    // Flipping a fleck edge-on thins it, which is what makes paper read as paper.
    context.scale(1, 0.55 + 0.45 * Math.abs(Math.cos(fleck.age * fleck.flutterRate)));
    context.fillStyle = toneFill(fleck.tone, alpha);
    context.fillRect(-fleck.width / 2, -fleck.height / 2, fleck.width, fleck.height);
    context.restore();
  }
}

/**
 * Decorative fleck layer for a tearing surface. The pool is created on the first
 * forward tear, so an untouched document allocates no Canvas work or frame loop.
 */
export default function TearDebrisCanvas({
  progress,
  quality,
  band = FULL_BAND,
  fronts = SINGLE_FRONT,
  seed = CANONICAL_TEAR_SEED,
  className,
}: TearDebrisCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fieldRef = useRef<DebrisField | null>(null);
  const surfaceRef = useRef<DebrisSurface | null>(null);
  const runRef = useRef<(() => void) | null>(null);
  const tearRef = useRef(0);
  const frameRef = useRef(0);
  const stampRef = useRef(0);

  const geometry = useMemo(() => createTearGeometry({ seed }), [seed]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    const tick = (timestamp: number) => {
      const field = fieldRef.current;
      const surface = surfaceRef.current;
      if (!field || !surface) {
        frameRef.current = 0;
        return;
      }

      const dt = stampRef.current ? (timestamp - stampRef.current) / 1000 : 0;
      stampRef.current = timestamp;
      stepDebris(field, dt, cullBounds(surface));
      paint(canvas, context, field, surface);

      if (field.flecks.length === 0) {
        frameRef.current = 0;
        stampRef.current = 0;
        return;
      }
      frameRef.current = window.requestAnimationFrame(tick);
    };

    runRef.current = () => {
      if (frameRef.current) return;
      stampRef.current = 0;
      frameRef.current = window.requestAnimationFrame(tick);
    };

    return () => {
      runRef.current = null;
      if (frameRef.current) window.cancelAnimationFrame(frameRef.current);
      frameRef.current = 0;
      stampRef.current = 0;
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const previous = tearRef.current;
    tearRef.current = progress;
    if (progress <= previous) return;

    const surface = measureSurface(canvas, geometry, band, surfaceRef.current);
    if (!surface) return;
    surfaceRef.current = surface;

    fieldRef.current ??= createDebrisField(
      (CANONICAL_TEAR_SEED ^ seed) >>> 0,
      quality,
      window.innerWidth,
    );
    const field = fieldRef.current;
    for (const front of fronts) {
      emitDebris(field, surface.geometry, front, previous, progress);
    }
    if (field.flecks.length > 0) runRef.current?.();
  }, [band, fronts, geometry, progress, quality, seed]);

  if (quality === "reduced") return null;

  return (
    <canvas
      ref={canvasRef}
      className={["narrative-tear-debris", className].filter(Boolean).join(" ")}
      aria-hidden="true"
      data-quality={quality}
    />
  );
}
