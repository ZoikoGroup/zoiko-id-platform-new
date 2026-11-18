import { describe, expect, it } from "vitest";

import { analyseFrame, cropToFrame, FRAME_ASPECT, hintFor } from "./capture-logic";

function frame(w: number, h: number, pixel: (x: number, y: number) => number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const v = pixel(x, y);
      out.set([v, v, v, 255], (y * w + x) * 4);
    }
  }
  return out;
}

describe("the guided capture's hints", () => {
  it("sees a white frame as glare, a black one as dark, and an empty one as nothing", () => {
    expect(analyseFrame(frame(20, 20, () => 255), 20, 20).glare).toBe(1);
    expect(analyseFrame(frame(20, 20, () => 0), 20, 20).dark).toBe(1);
    expect(analyseFrame(new Uint8ClampedArray(0), 0, 0)).toEqual({ glare: 0, dark: 0, sharpness: 0, brightness: 0 });
  });
  it("measures a flat frame as unsharp and a high-contrast one as sharp", () => {
    const flat = analyseFrame(frame(32, 32, () => 128), 32, 32);
    const checks = analyseFrame(frame(32, 32, (x, y) => ((x + y) % 2 === 0 ? 40 : 200)), 32, 32);
    expect(flat.sharpness).toBe(0);
    expect(checks.sharpness).toBeGreaterThan(1000);
  });
  it("says one thing at a time, in the order dark, glare, sharp", () => {
    expect(hintFor({ glare: 0.5, dark: 0.9, sharpness: 0, brightness: 10 }).text).toMatch(/too dark/);
    expect(hintFor({ glare: 0.5, dark: 0, sharpness: 0, brightness: 200 }).text).toMatch(/glare/);
    expect(hintFor({ glare: 0, dark: 0, sharpness: 1, brightness: 120 }).text).toMatch(/not sharp/);
    expect(hintFor({ glare: 0, dark: 0, sharpness: 80, brightness: 120 })).toMatchObject({ ok: true });
  });
  it("crops the middle of the picture to the passport page's shape, whatever the camera's shape", () => {
    for (const [w, h] of [[1920, 1080], [1080, 1920], [640, 480], [800, 800]] as const) {
      const c = cropToFrame(w, h);
      expect(c.sx + c.sw).toBeLessThanOrEqual(w);
      expect(c.sy + c.sh).toBeLessThanOrEqual(h);
      expect(Math.abs(c.sw / c.sh - FRAME_ASPECT)).toBeLessThan(0.02);
      expect(Math.abs(c.sx - (w - c.sw - c.sx))).toBeLessThanOrEqual(1);
    }
  });
});
