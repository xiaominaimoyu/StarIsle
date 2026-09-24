import type { NarrativeQuality } from "./quality";
import type { TearGeometry } from "./tear-geometry";

/**
 * Paper flecks released along a tearing path. Pure state + physics: no DOM, no
 * Canvas, no timing source, so the caller owns rAF, sizing and rendering.
 */

export interface DebrisFleck {
  x: number;
  y: number;
  vx: number;
  vy: number;
  width: number;
  height: number;
  angle: number;
  spin: number;
  age: number;
  ttl: number;
  tone: number;
  flutterPhase: number;
  flutterRate: number;
  flutterAmplitude: number;
}

export interface DebrisField {
  readonly budget: number;
  readonly density: number;
  flecks: DebrisFleck[];
  cursor: number;
  pending: number;
  random: () => number;
}

export interface DebrisConfig {
  readonly budget: number;
  readonly density: number;
}

/** A tear front travels from `origin` toward the edge its direction points at. */
export interface TearFront {
  readonly origin: number;
  readonly direction: 1 | -1;
}

export interface EmitOptions {
  readonly spread?: number;
  readonly maxPerFrame?: number;
}

export interface DebrisBounds {
  readonly width: number;
  readonly height: number;
}

export const DEBRIS_TONES = Object.freeze([
  { r: 217, g: 213, b: 205 },
  { r: 169, g: 129, b: 82 },
  { r: 125, g: 146, b: 181 },
  { r: 111, g: 90, b: 134 },
] as const);

const TONE_WEIGHTS = Object.freeze([0.58, 0.26, 0.13, 0.03] as const);

const GRAVITY = 236;
const DRAG = 2.05;
const SPIN_DRAG = 1.25;
const MIN_TTL = 1.05;
const MAX_TTL = 2.15;
const FADE_IN = 0.08;
const FADE_OUT = 0.45;
const MAX_STEP = 0.05;
const OFFSCREEN_MARGIN = 28;
const TAU = Math.PI * 2;

const CONFIG_BY_QUALITY: Readonly<Record<NarrativeQuality, DebrisConfig>> = Object.freeze({
  high: Object.freeze({ budget: 40, density: 0.3 }),
  medium: Object.freeze({ budget: 30, density: 0.24 }),
  low: Object.freeze({ budget: 22, density: 0.18 }),
  reduced: Object.freeze({ budget: 0, density: 0 }),
});

const COMPACT_VIEWPORT = 480;
const COMPACT_BUDGET = 22;
const COMPACT_DENSITY = 0.18;

function clamp(value: number, minimum: number, maximum: number): number {
  if (!Number.isFinite(value)) return minimum;
  return Math.min(maximum, Math.max(minimum, value));
}

function clampUnit(value: number): number {
  return clamp(value, 0, 1);
}

function createSeededRandom(seed: number): () => number {
  let state = seed >>> 0;

  return () => {
    state = (state + 0x6d2b_79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 0x1_0000_0000;
  };
}

export function debrisConfigFor(
  quality: NarrativeQuality,
  viewportWidth: number,
): DebrisConfig {
  const config = CONFIG_BY_QUALITY[quality];
  if (config.budget === 0) return config;
  if (!(viewportWidth < COMPACT_VIEWPORT)) return config;

  return Object.freeze({
    budget: Math.min(config.budget, COMPACT_BUDGET),
    density: Math.min(config.density, COMPACT_DENSITY),
  });
}

export function createDebrisField(
  seed: number,
  quality: NarrativeQuality,
  viewportWidth: number,
): DebrisField {
  const { budget, density } = debrisConfigFor(quality, viewportWidth);

  return { budget, density, flecks: [], cursor: 0, pending: 0, random: createSeededRandom(seed) };
}

export function resetDebris(field: DebrisField): void {
  field.flecks.length = 0;
  field.cursor = 0;
  field.pending = 0;
}

function pickTone(random: () => number): number {
  const roll = random();
  let threshold = 0;
  for (let index = 0; index < TONE_WEIGHTS.length; index += 1) {
    threshold += TONE_WEIGHTS[index];
    if (roll < threshold) return index;
  }
  return TONE_WEIGHTS.length - 1;
}

/**
 * Arc window a front has opened between two tear values, ordered low to high so
 * it feeds `TearGeometry.sampleAt` without the caller tracking directions.
 */
export function frontRange(
  front: TearFront,
  fromProgress: number,
  toProgress: number,
): readonly [number, number] {
  const origin = clampUnit(front.origin);
  const sweep = (front.direction === 1 ? 1 : 0) - origin;
  const from = origin + sweep * clampUnit(fromProgress);
  const to = origin + sweep * clampUnit(toProgress);

  return to < from ? [to, from] : [from, to];
}

function spawn(field: DebrisField, sample: ReturnType<TearGeometry["sampleAt"]>, travel: number): void {
  const { random } = field;
  const size = 2.8 + random() * 6.2;
  const normalSign = random() < 0.5 ? -1 : 1;
  const push = (24 + random() * 70) * normalSign;
  const carry = travel * (14 + random() * 48);

  const fleck: DebrisFleck = {
    x: sample.point.x + sample.normal.x * push * 0.02 + (random() - 0.5) * size * 1.8,
    y: sample.point.y + sample.normal.y * push * 0.02 + (random() - 0.5) * size * 1.2,
    vx: sample.normal.x * push + sample.tangent.x * carry + (random() - 0.5) * 22,
    vy: sample.normal.y * push + sample.tangent.y * carry - (16 + random() * 44),
    width: size,
    height: size * (0.42 + random() * 0.58),
    angle: random() * TAU,
    spin: (random() - 0.5) * 7.4,
    age: 0,
    ttl: MIN_TTL + random() * (MAX_TTL - MIN_TTL),
    tone: pickTone(random),
    flutterPhase: random() * TAU,
    flutterRate: 5.2 + random() * 4.6,
    flutterAmplitude: 16 + random() * 34,
  };

  if (field.flecks.length < field.budget) {
    field.flecks.push(fleck);
    return;
  }

  // Bounded pool: overwrite the oldest slot rather than growing past the budget.
  field.flecks[field.cursor] = fleck;
  field.cursor = (field.cursor + 1) % field.budget;
}

/**
 * Releases flecks across the arc opened since the previous tear value, so the
 * emission tracks the moving rip front instead of the viewport centre.
 */
export function emitDebris(
  field: DebrisField,
  geometry: TearGeometry,
  front: TearFront,
  previousProgress: number,
  progress: number,
  options: EmitOptions = {},
): number {
  if (field.budget === 0) return 0;

  const spread = clamp(options.spread ?? 1, 0, 1);
  const maxPerFrame = Math.max(1, Math.floor(options.maxPerFrame ?? 4));
  const previous = clampUnit(previousProgress);
  const current = clampUnit(progress);
  // Paper only sheds while the tear travels forward; a rollback is silent.
  if (current <= previous) return 0;

  const [low, high] = frontRange(front, previous, current);
  const arcDelta = (high - low) * geometry.totalLength * spread;
  if (arcDelta <= 0) return 0;

  field.pending += arcDelta * field.density;
  const wanted = Math.min(maxPerFrame, Math.floor(field.pending));
  if (wanted <= 0) return 0;

  field.pending -= wanted;
  for (let index = 0; index < wanted; index += 1) {
    spawn(field, geometry.sampleAt(low + field.random() * (high - low)), front.direction);
  }
  return wanted;
}

function flutterEnvelope(fleck: DebrisFleck): number {
  const life = fleck.age / fleck.ttl;
  return life < 0.3 ? 1 : 1 - ((life - 0.3) / 0.7);
}

/** Advances every fleck and drops the expired or escaped ones. */
export function stepDebris(field: DebrisField, dtSeconds: number, bounds: DebrisBounds): void {
  const dt = clamp(dtSeconds, 0, MAX_STEP);
  if (dt === 0) return;

  const kept: DebrisFleck[] = [];
  for (const fleck of field.flecks) {
    fleck.age += dt;
    if (fleck.age >= fleck.ttl) continue;

    const damping = Math.exp(-DRAG * dt);
    fleck.vx *= damping;
    fleck.vy = fleck.vy * damping + GRAVITY * dt;
    fleck.spin *= Math.exp(-SPIN_DRAG * dt);

    const flutter = Math.cos(fleck.age * fleck.flutterRate + fleck.flutterPhase)
      * fleck.flutterAmplitude * flutterEnvelope(fleck);

    fleck.x += (fleck.vx + flutter) * dt;
    fleck.y += fleck.vy * dt;
    fleck.angle += fleck.spin * dt;

    if (fleck.x < -OFFSCREEN_MARGIN || fleck.x > bounds.width + OFFSCREEN_MARGIN) continue;
    if (fleck.y > bounds.height + OFFSCREEN_MARGIN) continue;
    kept.push(fleck);
  }

  field.flecks = kept;
  if (field.cursor >= kept.length) field.cursor = 0;
}

/** Alpha over lifetime: a short pickup, then a long paper-thin fade-out. */
export function debrisAlpha(fleck: DebrisFleck): number {
  const life = clamp(fleck.age / fleck.ttl, 0, 1);
  if (life < FADE_IN) return life / FADE_IN;
  if (life > 1 - FADE_OUT) return (1 - life) / FADE_OUT;
  return 1;
}
