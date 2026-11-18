/** Rules for the live selfie capture. Pure, so they are tested without a camera. The platform ignores a face unless the deployment has an approved engine for the person's country,
 * so a screen built on this asks first (`canCapture`) and never opens the camera for a market that has no approved engine. */

/** The server's ceilings (verification/api/v2_self_run.py): a still up to 2 MB, a short capture up to 8 MB. */
export const MAX_PROBE_BYTES = 2 * 1024 * 1024;
export const MAX_LIVENESS_BYTES = 8 * 1024 * 1024;
/** How long the liveness capture runs, in milliseconds. Short on purpose: it is a presentation-attack check, not a recording of the person. */
export const LIVENESS_MS = 3000;

export type EngineStanding = { mode: "DISABLED" | "LAB_ONLY" | "APPROVED_FOR_SCOPE"; countries: string[] };

export type Gate = { allowed: true } | { allowed: false; reason: string };

/** Whether to offer the camera at all. Only an engine approved for the person's country makes a selfie useful; anywhere else it would collect an image to ignore. */
export function canCapture(engine: EngineStanding | undefined, country: string | null): Gate {
  if (!engine) return { allowed: false, reason: "We could not check whether a selfie is needed here. You can continue without one." };
  if (engine.mode !== "APPROVED_FOR_SCOPE") return { allowed: false, reason: "A selfie is not needed for this check. Nothing about your face is collected." };
  if (!country || !engine.countries.includes(country.toUpperCase())) return { allowed: false, reason: "A selfie is not needed for documents from this country." };
  return { allowed: true };
}

export type CaptureProblem = "NO_CAMERA" | "PERMISSION_DENIED" | "CAMERA_BUSY" | "NO_RECORDER" | "TOO_LARGE" | "EMPTY" | "UNKNOWN";

/** A browser media error in words a person can act on. The alternatives route (no camera, assistive technology) is always offered next to it. */
export function problemOf(error: unknown): CaptureProblem {
  const name = typeof error === "object" && error !== null && "name" in error ? String((error as { name: unknown }).name) : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "PERMISSION_DENIED";
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError") return "NO_CAMERA";
  if (name === "NotReadableError" || name === "AbortError") return "CAMERA_BUSY";
  return "UNKNOWN";
}

export const PROBLEM_TEXT: Record<CaptureProblem, string> = {
  NO_CAMERA: "No camera was found on this device.",
  PERMISSION_DENIED: "Camera access was blocked. Allow it for this site in your browser settings and try again.",
  CAMERA_BUSY: "The camera is in use by another app. Close it and try again.",
  NO_RECORDER: "This browser cannot record the short liveness capture.",
  TOO_LARGE: "The capture was too large to send. Move to better light, hold still and try again.",
  EMPTY: "Nothing was captured. Try again.",
  UNKNOWN: "The camera could not be used.",
};

/** The first recording format this browser supports, or null when it records none. */
export function pickRecorderType(isSupported: (type: string) => boolean): string | null {
  return ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm", "video/mp4"].find(isSupported) ?? null;
}

export type Face = { probe_base64: string; liveness_base64: string };

/** Check the two captures against the server's ceilings and return what finish takes. Anything empty or too large is a named problem, not a failed upload. */
export function toFace(probe: Uint8Array, liveness: Uint8Array): { ok: true; face: Face } | { ok: false; problem: CaptureProblem } {
  if (probe.length === 0 || liveness.length === 0) return { ok: false, problem: "EMPTY" };
  if (probe.length > MAX_PROBE_BYTES || liveness.length > MAX_LIVENESS_BYTES) return { ok: false, problem: "TOO_LARGE" };
  return { ok: true, face: { probe_base64: toBase64(probe), liveness_base64: toBase64(liveness) } };
}

export function toBase64(bytes: Uint8Array): string {
  let s = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) s += String.fromCharCode(...bytes.subarray(i, i + step));
  return btoa(s);
}
