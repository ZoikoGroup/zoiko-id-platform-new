/** The guided camera capture's rules (CR-124). Everything here is a HINT to help a person take a better photograph. The platform decides on the photograph it receives, never on these numbers. */

export type FrameAnalysis = { glare: number; dark: number; sharpness: number; brightness: number };

/** A passport's data page is about 125 by 88 millimetres: the on-screen frame keeps that shape so the page fills it and nothing else does. */
export const FRAME_ASPECT = 1.42;
/** The share of the width the frame takes. */
export const FRAME_WIDTH_SHARE = 0.9;

/** Hint thresholds on a small preview frame. They are starting points, unmeasured against real photographs; the server's own checks on the full photograph decide. */
export const HINT_LIMITS = { glareMax: 0.02, darkMax: 0.5, sharpMin: 12 } as const;

/** Glare (near-white share), darkness (near-black share), sharpness (variance of a Laplacian over the grey frame) and average brightness, from RGBA pixels. */
export function analyseFrame(rgba: ArrayLike<number>, width: number, height: number): FrameAnalysis {
  const n = width * height;
  if (n === 0 || rgba.length < n * 4) return { glare: 0, dark: 0, sharpness: 0, brightness: 0 };
  const grey = new Float32Array(n);
  let glare = 0;
  let dark = 0;
  let sum = 0;
  for (let i = 0; i < n; i += 1) {
    const g = 0.299 * rgba[i * 4]! + 0.587 * rgba[i * 4 + 1]! + 0.114 * rgba[i * 4 + 2]!;
    grey[i] = g;
    sum += g;
    if (g >= 245) glare += 1;
    if (g <= 20) dark += 1;
  }
  let mean = 0;
  let m2 = 0;
  let count = 0;
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const i = y * width + x;
      const lap = grey[i - 1]! + grey[i + 1]! + grey[i - width]! + grey[i + width]! - 4 * grey[i]!;
      count += 1;
      const d = lap - mean;
      mean += d / count;
      m2 += d * (lap - mean);
    }
  }
  return { glare: glare / n, dark: dark / n, sharpness: count > 1 ? m2 / (count - 1) : 0, brightness: sum / n };
}

/** What to tell the person right now. The first problem found is the one said, so the advice is one thing at a time. */
export function hintFor(a: FrameAnalysis): { ok: boolean; text: string } {
  if (a.dark > HINT_LIMITS.darkMax) return { ok: false, text: "It is too dark. Move to where there is more light." };
  if (a.glare > HINT_LIMITS.glareMax) return { ok: false, text: "There is glare. Tilt the page a little so the light does not bounce into the camera." };
  if (a.sharpness < HINT_LIMITS.sharpMin) return { ok: false, text: "It is not sharp. Hold still and move a little closer or further away until the text is crisp." };
  return { ok: true, text: "That looks good. Keep the page flat inside the frame and take the photograph." };
}

/** The part of the camera picture inside the on-screen frame, centred, so what is sent is what the person framed. */
export function cropToFrame(videoWidth: number, videoHeight: number): { sx: number; sy: number; sw: number; sh: number } {
  let sw = Math.floor(videoWidth * FRAME_WIDTH_SHARE);
  let sh = Math.floor(sw / FRAME_ASPECT);
  if (sh > videoHeight) {
    sh = Math.floor(videoHeight * FRAME_WIDTH_SHARE);
    sw = Math.floor(sh * FRAME_ASPECT);
  }
  return { sx: Math.floor((videoWidth - sw) / 2), sy: Math.floor((videoHeight - sh) / 2), sw, sh };
}
