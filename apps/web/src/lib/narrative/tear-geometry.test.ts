import { describe, expect, it } from "vitest";
import {
  CANONICAL_TEAR_SEED,
  createTearGeometry,
  getTearFilterRegion,
  projectTearGeometry,
  scaleTearPath,
} from "./tear-geometry";

describe("tear geometry", () => {
  it("generates one deterministic normalized path per seed", () => {
    const first = createTearGeometry({ seed: 20260909 });
    const second = createTearGeometry({ seed: 20260909 });
    const other = createTearGeometry({ seed: 20260910 });

    expect(first.path).toBe(second.path);
    expect(first.points).toEqual(second.points);
    expect(first.cumulativeLength).toEqual(second.cumulativeLength);
    expect(first.bounds).toEqual(second.bounds);
    expect(first.path).not.toBe(other.path);
    expect(createTearGeometry().path).toBe(
      createTearGeometry({ seed: CANONICAL_TEAR_SEED }).path,
    );
    expect(createTearGeometry({ seed: 0 }).path).not.toBe(
      createTearGeometry({ seed: 0x9e37_79b9 }).path,
    );
    expect(first.points[0]).toEqual({ x: 0, y: 0.5 });
    expect(first.points.at(-1)).toEqual({ x: 1, y: 0.5 });
    expect(first.bounds.minX).toBe(0);
    expect(first.bounds.maxX).toBe(1);
    expect(Object.isFrozen(first.points)).toBe(true);
    expect(Object.isFrozen(first.cumulativeLength)).toBe(true);
    expect(Object.isFrozen(first.bounds)).toBe(true);

    for (let index = 0; index < first.points.length; index += 1) {
      const point = first.points[index];
      expect(point.x).toBeGreaterThanOrEqual(0);
      expect(point.x).toBeLessThanOrEqual(1);
      expect(point.y).toBeGreaterThanOrEqual(0);
      expect(point.y).toBeLessThanOrEqual(1);
      if (index > 0) {
        expect(point.x).toBeGreaterThan(first.points[index - 1].x);
        expect(first.cumulativeLength[index]).toBeGreaterThan(
          first.cumulativeLength[index - 1],
        );
      }
    }
  });

  it("samples point, unit tangent, and perpendicular normal by arc length", () => {
    const geometry = createTearGeometry({ seed: 37 });
    const start = geometry.sampleAt(0);
    const middle = geometry.sampleAt(0.5);
    const end = geometry.sampleAt(1);

    expect(start.point).toEqual(geometry.points[0]);
    expect(end.point).toEqual(geometry.points.at(-1));
    expect(middle.point.x).toBeGreaterThan(0);
    expect(middle.point.x).toBeLessThan(1);

    for (const sample of [start, middle, end]) {
      expect(Math.hypot(sample.tangent.x, sample.tangent.y)).toBeCloseTo(1, 8);
      expect(Math.hypot(sample.normal.x, sample.normal.y)).toBeCloseTo(1, 8);
      expect(
        (sample.tangent.x * sample.normal.x)
        + (sample.tangent.y * sample.normal.y),
      ).toBeCloseTo(0, 8);
    }
  });

  it("clamps unsafe progress instead of leaking invalid coordinates", () => {
    const geometry = createTearGeometry();

    expect(geometry.sampleAt(-1).point).toEqual(geometry.points[0]);
    expect(geometry.sampleAt(Number.NaN).point).toEqual(geometry.points[0]);
    expect(geometry.sampleAt(2).point).toEqual(geometry.points.at(-1));
  });

  it("projects path and sampler into the same non-square SVG coordinate space", () => {
    const geometry = createTearGeometry();
    const projected = projectTearGeometry(geometry, 1000, 240);
    const path = scaleTearPath(geometry, 1000, 240);
    const region = getTearFilterRegion(projected, 18);
    const projectedMiddle = projected.sampleAt(0.5);
    const normalizedMiddle = geometry.sampleAt(0.5);
    const naivelyScaledMiddle = {
      x: normalizedMiddle.point.x * 1000,
      y: normalizedMiddle.point.y * 240,
    };

    expect(path).toBe(projected.path);
    expect(path).toMatch(/^M 0 120 L /);
    expect(path).toMatch(/L 1000 120$/);
    expect(projectedMiddle.point.x).not.toBeCloseTo(naivelyScaledMiddle.x, 1);
    expect(projectedMiddle.point.y).not.toBeCloseTo(naivelyScaledMiddle.y, 1);
    expect(Math.hypot(projectedMiddle.tangent.x, projectedMiddle.tangent.y)).toBeCloseTo(1, 8);
    expect(
      (projectedMiddle.tangent.x * projectedMiddle.normal.x)
      + (projectedMiddle.tangent.y * projectedMiddle.normal.y),
    ).toBeCloseTo(0, 8);
    expect(region.x).toBe(0);
    expect(region.width).toBe(1000);
    expect(region.y).toBeGreaterThanOrEqual(0);
    expect(region.y + region.height).toBeLessThanOrEqual(240);
    expect(region.height).toBeLessThan(240 * 0.6);
  });

  it("rejects geometry options that break normalized bounds", () => {
    expect(() => createTearGeometry({ segmentCount: 3 })).toThrow(RangeError);
    expect(() => createTearGeometry({ segmentCount: 12.5 })).toThrow(RangeError);
    expect(() => createTearGeometry({ seed: -1 })).toThrow(RangeError);
    expect(() => createTearGeometry({ seed: 1.5 })).toThrow(RangeError);
    expect(() => createTearGeometry({ baseline: 1.2 })).toThrow(RangeError);
    expect(() => createTearGeometry({ baseline: 0.1, amplitude: 0.2 })).toThrow(RangeError);
    expect(() => projectTearGeometry(createTearGeometry(), 0, 100)).toThrow(RangeError);
  });
});
