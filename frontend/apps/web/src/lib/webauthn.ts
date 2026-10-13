"use client";

import { browserSupportsWebAuthn, startAuthentication, startRegistration } from "@simplewebauthn/browser";
import { ApiError } from "@zoiko/api-client";

export { browserSupportsWebAuthn };

/** Readable message for a WebAuthn ceremony failure (DOMException names) or a cell error. */
export function passkeyErrorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) {
    switch (e.name) {
      case "NotAllowedError":
        return "The passkey prompt was dismissed or timed out.";
      case "InvalidStateError":
        return "This passkey is already registered on this account.";
      case "SecurityError":
        return "Passkeys are not allowed on this address. Contact your administrator.";
      case "AbortError":
        return "The passkey prompt was cancelled.";
      default:
        return e.message;
    }
  }
  return "The passkey check failed.";
}

/** Runs an authentication ceremony from the cell's options JSON. */
export function getPasskey(optionsJSON: unknown) {
  return startAuthentication({ optionsJSON: optionsJSON as Parameters<typeof startAuthentication>[0]["optionsJSON"] });
}

/** Runs a registration ceremony from the cell's options JSON. */
export function createPasskey(optionsJSON: unknown) {
  return startRegistration({ optionsJSON: optionsJSON as Parameters<typeof startRegistration>[0]["optionsJSON"] });
}

/** After a cell sign-in (passkey verify / step-up), mint this app's session from the cell cookie. */
export async function completeAppSession(): Promise<void> {
  const res = await fetch("/api/auth/session", { method: "POST", credentials: "same-origin" });
  if (!res.ok) throw await ApiError.from(res);
}
