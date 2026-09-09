export interface TearPoint {
  readonly x: number;
  readonly y: number;
}

export interface TearSample {
  readonly point: TearPoint;
  readonly tangent: TearPoint;
  readonly normal: TearPoint;
}

export interface TearGeometry {
  readonly points: ReadonlyArray<TearPoint>;
  readonly path: string;
  readonly cumulativeLength: ReadonlyArray<number>;
  readonly totalLength: number;
  readonly bounds: TearBounds;
  readonly sampleAt: (progress: number) => TearSample;
}

export interface ProjectedTearGeometry extends TearGeometry {
  readonly width: number;
  readonly height: number;
}

export interface TearGeometryOptions {
  readonly seed?: number;
  readonly segmentCount?: number;
  readonly baseline?: number;
  readonly amplitude?: number;
}

export interface TearFilterRegion {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface TearBounds {
  readonly minX: number;
  readonly minY: number;
  readonly maxX: number;
  readonly maxY: number;
}

export const CANONICAL_TEAR_SEED = 0x5354_4152;
const DEFAULT_SEGMENT_COUNT = 24;
const DEFAULT_BASELINE = 0.5;
const DEFAULT_AMPLITUDE = 0.16;
const COORDINATE_PRECISION = 6;
const LENGTH_PRECISION = 12;

function roundCoordinate(value: number): number {
  return Number(value.toFixed(COORDINATE_PRECISION));
}

function roundLength(value: number): number {
  return Number(value.toFixed(LENGTH_PRECISION));
}

function formatCoordinate(value: number): string {
  return String(roundCoordinate(value));
}

function assertFiniteInRange(
  name: string,
  value: number,
  minimum: number,
  maximum: number,
): void {
  if (!Number.isFinite(value) || value < minimum || value > maximum) {
    throw new RangeError(`${name} must be between ${minimum} and ${maximum}`);
  }
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

function createPath(points: ReadonlyArray<TearPoint>): string {
  return points
    .map((point, index) => {
      const command = index === 0 ? "M" : "L";
      return `${command} ${formatCoordinate(point.x)} ${formatCoordinate(point.y)}`;
    })
    .join(" ");
}

function clampProgress(progress: number): number {
  if (!Number.isFinite(progress)) return 0;
  return Math.min(1, Math.max(0, progress));
}

function createSampler(
  points: ReadonlyArray<TearPoint>,
  cumulativeLength: ReadonlyArray<number>,
  totalLength: number,
): (progress: number) => TearSample {
  return (progress) => {
    const targetLength = clampProgress(progress) * totalLength;
    let low = 1;
    let high = cumulativeLength.length - 1;

    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (cumulativeLength[middle] < targetLength) {
        low = middle + 1;
      } else {
        high = middle;
      }
    }

    const endIndex = low;
    const startIndex = endIndex - 1;
    const start = points[startIndex];
    const end = points[endIndex];
    const segmentLength = cumulativeLength[endIndex] - cumulativeLength[startIndex];
    const segmentProgress = segmentLength === 0
      ? 0
      : (targetLength - cumulativeLength[startIndex]) / segmentLength;
    const deltaX = end.x - start.x;
    const deltaY = end.y - start.y;
    const directionLength = Math.hypot(deltaX, deltaY) || 1;
    const tangent = Object.freeze({
      x: deltaX / directionLength,
      y: deltaY / directionLength,
    });

    return Object.freeze({
      point: Object.freeze({
        x: start.x + (deltaX * segmentProgress),
        y: start.y + (deltaY * segmentProgress),
      }),
      tangent,
      normal: Object.freeze({
        x: -tangent.y,
        y: tangent.x,
      }),
    });
  };
}

function createGeometryFromPoints(points: ReadonlyArray<TearPoint>): TearGeometry {
  const immutablePoints = Object.freeze(points.map((point) => Object.freeze({
    x: roundCoordinate(point.x),
    y: roundCoordinate(point.y),
  })));
  const lengths: number[] = [0];
  for (let index = 1; index < immutablePoints.length; index += 1) {
    const previous = immutablePoints[index - 1];
    const current = immutablePoints[index];
    lengths.push(roundLength(
      lengths[index - 1] + Math.hypot(current.x - previous.x, current.y - previous.y),
    ));
  }
  const cumulativeLength = Object.freeze(lengths);
  const totalLength = cumulativeLength.at(-1) ?? 0;
  const pointX = immutablePoints.map((point) => point.x);
  const pointY = immutablePoints.map((point) => point.y);
  const bounds = Object.freeze({
    minX: Math.min(...pointX),
    minY: Math.min(...pointY),
    maxX: Math.max(...pointX),
    maxY: Math.max(...pointY),
  });

  return Object.freeze({
    points: immutablePoints,
    path: createPath(immutablePoints),
    cumulativeLength,
    totalLength,
    bounds,
    sampleAt: createSampler(immutablePoints, cumulativeLength, totalLength),
  });
}

export function createTearGeometry(options: TearGeometryOptions = {}): TearGeometry {
  const seed = options.seed ?? CANONICAL_TEAR_SEED;
  const segmentCount = options.segmentCount ?? DEFAULT_SEGMENT_COUNT;
  const baseline = options.baseline ?? DEFAULT_BASELINE;
  const amplitude = options.amplitude ?? DEFAULT_AMPLITUDE;

  if (!Number.isInteger(seed) || seed < 0 || seed > 0xffff_ffff) {
    throw new RangeError("seed must be an unsigned 32-bit integer");
  }
  if (!Number.isInteger(segmentCount) || segmentCount < 4 || segmentCount > 96) {
    throw new RangeError("segmentCount must be an integer between 4 and 96");
  }
  assertFiniteInRange("baseline", baseline, 0, 1);
  assertFiniteInRange("amplitude", amplitude, 0.01, 0.35);
  if (baseline - amplitude < 0 || baseline + amplitude > 1) {
    throw new RangeError("baseline and amplitude must keep the path inside normalized bounds");
  }

  const random = createSeededRandom(seed);
  const points: TearPoint[] = [{ x: 0, y: baseline }];
  const cellWidth = 1 / segmentCount;

  for (let index = 1; index < segmentCount; index += 1) {
    const progress = index / segmentCount;
    const xJitter = (random() - 0.5) * cellWidth * 0.36;
    const envelope = Math.sin(Math.PI * progress) ** 0.65;
    const noise = (random() * 2) - 1;
    const grain = Math.sin(progress * Math.PI * 7) * 0.22;
    const y = baseline + (amplitude * envelope * ((noise * 0.78) + grain));

    points.push({
      x: progress + xJitter,
      y: Math.min(baseline + amplitude, Math.max(baseline - amplitude, y)),
    });
  }

  points.push({ x: 1, y: baseline });
  return createGeometryFromPoints(points);
}

export function projectTearGeometry(
  geometry: TearGeometry,
  width: number,
  height: number,
): ProjectedTearGeometry {
  assertFiniteInRange("width", width, Number.EPSILON, Number.MAX_SAFE_INTEGER);
  assertFiniteInRange("height", height, Number.EPSILON, Number.MAX_SAFE_INTEGER);
  const projected = createGeometryFromPoints(geometry.points.map((point) => ({
    x: point.x * width,
    y: point.y * height,
  })));

  return Object.freeze({ ...projected, width, height });
}

export function scaleTearPath(
  geometry: TearGeometry,
  width: number,
  height: number,
): string {
  return projectTearGeometry(geometry, width, height).path;
}

export function getTearFilterRegion(
  geometry: ProjectedTearGeometry,
  padding: number,
): TearFilterRegion {
  assertFiniteInRange("padding", padding, 0, Math.max(geometry.width, geometry.height));

  const minimumY = geometry.bounds.minY;
  const maximumY = geometry.bounds.maxY;
  const y = Math.max(0, minimumY - padding);
  const bottom = Math.min(geometry.height, maximumY + padding);

  return Object.freeze({
    x: roundCoordinate(Math.max(0, geometry.bounds.minX - padding)),
    y: roundCoordinate(y),
    width: roundCoordinate(
      Math.min(geometry.width, geometry.bounds.maxX + padding)
      - Math.max(0, geometry.bounds.minX - padding),
    ),
    height: roundCoordinate(bottom - y),
  });
}
