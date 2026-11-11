/** First-party device cookie: a random id the web app keeps for the browser so the cell can count sign-up, verification and claim
 * attempts per device (fraud and abuse prevention). It holds 128 random bits and no personal data. It is HttpOnly (scripts cannot
 * read it), is never sent to the cell as a cookie, and reaches the cell only as the `x-zid-device` header set here. The browser never
 * chooses it: any `x-zid-device` it sends is dropped. */
import { randomBytes } from "node:crypto";

import { readCookie, serializeCookie } from "./cookies";

export const DEVICE_HEADER = "x-zid-device";
export const DEVICE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60;

const DEVICE_ID = /^[0-9a-f]{32}$/;

export type DeviceIdentity = {
  /** The id to forward upstream, or null when the cookie is switched off. */
  id: string | null;
  /** Set-Cookie value, present only when a new id was minted for this request. */
  setCookie: string | null;
};

export function deviceCookieName(secure: boolean): string {
  return secure ? "__Host-zid_device" : "zid_device";
}

export function isDeviceId(value: string | null | undefined): value is string {
  return !!value && DEVICE_ID.test(value);
}

/** Reuses a well-formed id from the browser's cookie header; otherwise mints one (and says to set it). */
export function resolveDevice(cookieHeader: string | null, secure: boolean, enabled = true): DeviceIdentity {
  if (!enabled) return { id: null, setCookie: null };
  const name = deviceCookieName(secure);
  const held = readCookie(cookieHeader, name);
  if (isDeviceId(held)) return { id: held, setCookie: null };
  const id = randomBytes(16).toString("hex");
  return { id, setCookie: serializeCookie(name, id, DEVICE_COOKIE_MAX_AGE, secure) };
}

/** Sets the upstream header from the BFF's own value, first removing whatever the client sent. */
export function applyDevice(headers: Headers, device: DeviceIdentity): void {
  headers.delete(DEVICE_HEADER);
  if (device.id) headers.set(DEVICE_HEADER, device.id);
}
