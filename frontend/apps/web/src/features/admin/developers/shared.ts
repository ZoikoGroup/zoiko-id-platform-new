export const ENDPOINTS_KEY = ["admin", "developers", "webhook-endpoints"];
export const DELIVERIES_KEY = ["admin", "developers", "webhook-deliveries"];
export const EVENT_TYPES_KEY = ["admin", "developers", "webhook-event-types"];
export const SIGNING_KEYS_KEY = ["admin", "developers", "webhook-signing-keys"];

/** `com.zoiko.id.mandate.revoked.v1` becomes `mandate.revoked`. */
export function shortEvent(type: string): string {
  return type.replace(/^com\.zoiko\.id\./, "").replace(/\.v\d+$/, "");
}
