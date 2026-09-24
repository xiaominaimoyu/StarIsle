import { describe, expect, it } from "vitest";
import {
  createDebrisField,
  debrisAlpha,
  debrisConfigFor,
  emitDebris,
  frontRange,
  resetDebris,
  stepDebris,
  type DebrisField,
  type TearFront,
} from "./tear-debris";
import { createTearGeometry, projectTearGeometry } from "./tear-geometry";

const SURFACE_WIDTH = 900;
const SURFACE_HEIGHT = 120;
const geometry = projectTearGeometry(createTearGeometry(), SURFACE_WIDTH, SURFACE_HEIGHT);
const singleFront = { origin: 0, direction: 1 } as const;
const leftFront = { origin: 0.5, direction: -1 } as const;
const rightFront = { origin: 0.5, direction: 1 } as const;
const bounds = { width: SURFACE_WIDTH, height: SURFACE_HEIGHT };

function field(quality: "high" | "medium" | "low" | "reduced" = "high", width = 1280, seed = 20260924) {
  return createDebrisField(seed, quality, width);
}

/** Drives a tear from 0 to 1 in `frames` steps, emitting on every step. */
function sweep(target: DebrisField, front: TearFront = singleFront, frames = 40): number {
  let emitted = 0;
  for (let index = 1; index <= frames; index += 1) {
    emitted += emitDebris(target, geometry, front, (index - 1) / frames, index / frames);
  }
  return emitted;
}

describe("tear debris configuration", () => {
  it("sizes the fleck pool from quality and viewport, and disables reduced motion", () => {
    expect(debrisConfigFor("high", 1440)).toEqual({ budget: 40, density: 0.3 });
    expect(debrisConfigFor("medium", 900).budget).toBe(30);
    expect(debrisConfigFor("low", 700).budget).toBe(22);
    expect(debrisConfigFor("reduced", 1440)).toEqual({ budget: 0, density: 0 });

    // Narrow touch viewports take the compact ceiling instead of the desktop pool.
    expect(debrisConfigFor("high", 390)).toEqual({ budget: 22, density: 0.18 });
    expect(debrisConfigFor("low", 390)).toEqual({ budget: 22, density: 0.18 });
    expect(debrisConfigFor("medium", 479).budget).toBe(22);
    expect(debrisConfigFor("reduced", 390)).toEqual({ budget: 0, density: 0 });
    expect(debrisConfigFor("high", Number.NaN).budget).toBe(40);
  });

  it("emits nothing when the pool is disabled", () => {
    const target = field("reduced");

    expect(target.budget).toBe(0);
    expect(sweep(target)).toBe(0);
    expect(target.flecks).toHaveLength(0);
  });
});

describe("tear debris fronts", () => {
  it("orders the opened arc low to high for either tear direction", () => {
    expect(frontRange(singleFront, 0, 0.4)).toEqual([0, 0.4]);
    expect(frontRange(singleFront, 0.4, 0.2)).toEqual([0.2, 0.4]);
    expect(frontRange(rightFront, 0, 1)).toEqual([0.5, 1]);
    expect(frontRange(leftFront, 0, 1)).toEqual([0, 0.5]);
    expect(frontRange(leftFront, 0.25, 0.75)).toEqual([0.125, 0.375]);
  });

  it("clamps non-finite and out-of-range tear values", () => {
    expect(frontRange(singleFront, Number.NaN, 5)).toEqual([0, 1]);
    expect(frontRange(rightFront, -2, -1)).toEqual([0.5, 0.5]);
    expect(frontRange(rightFront, -2, 1)).toEqual([0.5, 1]);
  });

  it("keeps the whole sweep inside the path so late frames never restart at the centre", () => {
    const target = field();
    sweep(target, singleFront);

    expect(target.flecks.length).toBeGreaterThan(0);
    for (const fleck of target.flecks) {
      expect(fleck.x).toBeGreaterThanOrEqual(-SURFACE_WIDTH);
      expect(fleck.x).toBeLessThanOrEqual(SURFACE_WIDTH * 2);
    }
  });

  it("releases flecks along the arc that just opened, not along the whole path", () => {
    const target = field();
    const before = emitDebris(target, geometry, singleFront, 0, 0.06);
    const openedX = target.flecks.map((fleck) => fleck.x);

    expect(before).toBeGreaterThan(0);
    // 6% of the arc stays on the far left, so nothing may have spawned beyond it.
    for (const x of openedX) expect(x).toBeLessThan(SURFACE_WIDTH * 0.2);

    const earlier = [...target.flecks];
    emitDebris(target, geometry, singleFront, 0.06, 0.12);
    const advanced = target.flecks.filter((fleck) => !earlier.includes(fleck));
    for (const fleck of advanced) {
      expect(fleck.x).toBeGreaterThanOrEqual(SURFACE_WIDTH * 0.03);
    }
  });

  it("splits a centre-origin tear into two outward fronts", () => {
    const target = field();
    const frames = 40;
    for (let index = 1; index <= frames; index += 1) {
      const from = (index - 1) / frames;
      const to = index / frames;
      emitDebris(target, geometry, leftFront, from, to);
      emitDebris(target, geometry, rightFront, from, to);
    }

    const xs = target.flecks.map((fleck) => fleck.x);
    expect(xs.length).toBeGreaterThan(4);
    // Both halves keep shedding, so the band spans more than one side of the path.
    expect(Math.min(...xs)).toBeLessThan(SURFACE_WIDTH * 0.4);
    expect(Math.max(...xs)).toBeGreaterThan(SURFACE_WIDTH * 0.6);
  });

  it("carries the tear direction into the horizontal velocity", () => {
    const forward = field("high", 1280, 11);
    emitDebris(forward, geometry, singleFront, 0, 1, { maxPerFrame: 40 });
    const backward = field("high", 1280, 11);
    emitDebris(backward, geometry, { origin: 1, direction: -1 }, 0, 1, { maxPerFrame: 40 });

    expect(forward.flecks.length).toBeGreaterThan(0);
    expect(backward.flecks.length).toBe(forward.flecks.length);
    for (let index = 0; index < forward.flecks.length; index += 1) {
      expect(backward.flecks[index].vx).toBeLessThan(forward.flecks[index].vx);
    }
  });
});

describe("tear debris pool", () => {
  it("never exceeds the budget however far the tear travels", () => {
    for (const quality of ["high", "medium", "low"] as const) {
      const target = field(quality);
      for (let round = 0; round < 12; round += 1) sweep(target, singleFront);
      expect(target.flecks.length).toBeLessThanOrEqual(target.budget);
      expect(target.flecks.length).toBeGreaterThan(0);
      expect(target.flecks.every((fleck) => fleck !== undefined)).toBe(true);
    }
  });

  it("caps emission per frame so a fast flick cannot dump the pool at once", () => {
    const target = field();

    expect(emitDebris(target, geometry, singleFront, 0, 1)).toBeLessThanOrEqual(4);
    expect(target.flecks.length).toBeLessThanOrEqual(4);
    expect(emitDebris(target, geometry, singleFront, 0, 1, { maxPerFrame: 1 })).toBe(1);
  });

  it("ignores zero-length and non-advancing tears", () => {
    const target = field();

    expect(emitDebris(target, geometry, singleFront, 0.4, 0.4)).toBe(0);
    expect(emitDebris(target, geometry, singleFront, 0.4, 0)).toBe(0);
    expect(emitDebris(target, geometry, singleFront, 0, 0.2, { spread: 0 })).toBe(0);
    expect(target.flecks).toHaveLength(0);
  });

  it("accumulates sub-fleck distances instead of losing them", () => {
    const target = field();
    let emitted = 0;
    for (let index = 0; index < 200; index += 1) {
      emitted += emitDebris(target, geometry, singleFront, 0, 0.0005);
    }
    expect(emitted).toBeGreaterThan(0);
    expect(target.pending).toBeGreaterThanOrEqual(0);
    expect(target.pending).toBeLessThan(1);
  });

  it("clears the pool and its fractional carry", () => {
    const target = field();
    sweep(target, singleFront);

    resetDebris(target);
    expect(target.flecks).toHaveLength(0);
    expect(target.pending).toBe(0);
    expect(target.cursor).toBe(0);
  });
});

describe("tear debris physics", () => {
  it("drops expired flecks and lets the rest fall", () => {
    const target = field();
    emitDebris(target, geometry, singleFront, 0, 1, { maxPerFrame: 20 });
    const live = target.flecks.length;
    expect(live).toBeGreaterThan(0);

    const [first] = target.flecks;
    const startY = first.y;
    stepDebris(target, 1 / 60, bounds);

    expect(target.flecks.length).toBe(live);
    expect(first.vy).toBeGreaterThan(-400);
    expect(first.y).toBeGreaterThan(startY - 1);

    for (let index = 0; index < 200; index += 1) stepDebris(target, 1 / 30, bounds);
    expect(target.flecks).toHaveLength(0);
  });

  it("culls flecks that leave the surface", () => {
    const target = field();
    emitDebris(target, geometry, singleFront, 0, 1, { maxPerFrame: 20 });
    for (const fleck of target.flecks) {
      fleck.x = -900;
      fleck.y = 0;
    }

    stepDebris(target, 1 / 60, bounds);
    expect(target.flecks).toHaveLength(0);
  });

  it("clamps hostile frame gaps so a background tab cannot teleport the paper", () => {
    const target = field();
    emitDebris(target, geometry, singleFront, 0, 1, { maxPerFrame: 6 });
    const snapshot = target.flecks.map((fleck) => ({ x: fleck.x, y: fleck.y }));

    stepDebris(target, 30, bounds);
    for (let index = 0; index < target.flecks.length; index += 1) {
      expect(Math.abs(target.flecks[index].x - snapshot[index].x)).toBeLessThan(30);
      expect(Math.abs(target.flecks[index].y - snapshot[index].y)).toBeLessThan(30);
    }

    const stationary = field();
    emitDebris(stationary, geometry, singleFront, 0, 1, { maxPerFrame: 6 });
    const before = stationary.flecks.map((fleck) => fleck.y);
    stepDebris(stationary, Number.NaN, bounds);
    expect(stationary.flecks.map((fleck) => fleck.y)).toEqual(before);
  });

  it("keeps the same seed reproducible", () => {
    const first = field("high", 1280, 4242);
    const second = field("high", 1280, 4242);
    const other = field("high", 1280, 4243);
    sweep(first, rightFront);
    sweep(second, rightFront);
    sweep(other, rightFront);
    stepDebris(first, 1 / 60, bounds);
    stepDebris(second, 1 / 60, bounds);

    expect(JSON.stringify(first.flecks)).toBe(JSON.stringify(second.flecks));
    expect(JSON.stringify(first.flecks)).not.toBe(JSON.stringify(other.flecks));
  });
});

describe("tear debris fade", () => {
  it("ramps in quickly and thins out across the remaining life", () => {
    const target = field();
    emitDebris(target, geometry, singleFront, 0, 1, { maxPerFrame: 8 });
    const [fleck] = target.flecks;

    fleck.age = 0;
    expect(debrisAlpha(fleck)).toBe(0);
    fleck.age = fleck.ttl * 0.1;
    expect(debrisAlpha(fleck)).toBeCloseTo(1, 6);
    fleck.age = fleck.ttl * 0.5;
    expect(debrisAlpha(fleck)).toBe(1);
    fleck.age = fleck.ttl * 0.9;
    expect(debrisAlpha(fleck)).toBeLessThan(0.3);
    fleck.age = fleck.ttl;
    expect(debrisAlpha(fleck)).toBe(0);

    // A corrupt age renders nothing rather than flashing a fleck back to life.
    fleck.age = Number.NaN;
    expect(debrisAlpha(fleck)).toBe(0);
  });

  it("assigns every tone weight a share of the paper", () => {
    const target = field();
    const seen = new Set<number>();
    for (let index = 0; index < 400; index += 1) {
      resetDebris(target);
      emitDebris(target, geometry, singleFront, 0, 1, { maxPerFrame: 40 });
      for (const fleck of target.flecks) {
        seen.add(fleck.tone);
        expect(fleck.tone).toBeGreaterThanOrEqual(0);
        expect(fleck.tone).toBeLessThan(4);
      }
    }
    expect(seen.size).toBe(4);
  });
});
