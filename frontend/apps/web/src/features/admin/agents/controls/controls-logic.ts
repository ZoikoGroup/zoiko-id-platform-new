import type { CredentialVerdict, IdpBinding, StopScope, ToolActionState } from "@zoiko/api-client";

/** The plain-language side of agent controls (ZID-CP-WF07, CR-169). The cell checks every rule again. */

export const SCOPES: { id: StopScope; label: string; subjectLabel: string | null; help: string }[] = [
  { id: "AGENT", label: "One agent (and its sub-agents)", subjectLabel: "Agent ID", help: "Stops the agent and every agent below it." },
  { id: "SPONSOR", label: "Every agent one person vouches for", subjectLabel: "Sponsor's principal ID", help: "Use when a sponsor has left or lost trust." },
  { id: "BLUEPRINT", label: "Every agent built from one blueprint", subjectLabel: "Blueprint, or blueprint@version", help: "Use when a blueprint release turns out to be unsafe." },
  { id: "ISSUER", label: "Everything bound to an outside provider", subjectLabel: "Issuer address, or issuer#key-id", help: "Withdraws the provider's bindings too. Add #key-id to stop only agents bound with that key." },
  { id: "TENANT", label: "The whole company", subjectLabel: null, help: "Every active agent stops. Use in an emergency." },
];

export const scopeInfo = (id: StopScope) => SCOPES.find((s) => s.id === id)!;

/** What is still needed before a stop can be asked for. */
export function stopProblem(scope: StopScope, subject: string, reason: string): string | null {
  const info = scopeInfo(scope);
  if (info.subjectLabel && !subject.trim()) return `Enter the ${info.subjectLabel.toLowerCase()}.`;
  if (scope === "ISSUER" && !subject.trim().startsWith("https://")) return "An issuer is an https address.";
  if (reason.trim().replace(/\s+/g, " ").length < 3) return "Say why, in a few words. The person who closes the stop reads it.";
  return null;
}

/** A stop is never described as complete while any place is not told. */
export function enforcementText(enforced: boolean, unsupported: number): string {
  return enforced ? "Enforced at every point we can vouch for." : `Enforced at the tool gateway. ${unsupported} tool ${unsupported === 1 ? "server is" : "servers are"} not told to stop work already begun.`;
}

export const ACTION_STATE: Record<ToolActionState, { label: string; tone: "info" | "warn" | "ok" | "danger" | "neutral" }> = {
  AUTHORIZED: { label: "Authorized, not yet run", tone: "info" },
  DISPATCHED: { label: "In progress", tone: "warn" },
  EXECUTED: { label: "Done, as the tool reported", tone: "ok" },
  FAILED: { label: "Did not happen", tone: "neutral" },
  UNKNOWN: { label: "Outcome unknown", tone: "danger" },
  COMPENSATED: { label: "Undone, as the tool reported", tone: "neutral" },
};

export const STATUS_OPTIONS = [{ id: "", label: "Any state" }, ...(Object.keys(ACTION_STATE) as ToolActionState[]).map((id) => ({ id, label: ACTION_STATE[id].label }))];

/** An unknown outcome is not a success and not a failure; capacity held for it stays held. */
export const unknownNote = "The tool's server has not said whether this happened. Any capacity held for it stays held until someone reconciles it.";

export const BINDING_STATE: Record<IdpBinding["state"], { label: string; tone: "ok" | "warn" | "danger" | "neutral" }> = {
  ACTIVE: { label: "Heard from lately", tone: "ok" },
  STALE: { label: "Not heard from lately", tone: "warn" },
  EXPIRED: { label: "Expired", tone: "neutral" },
  REVOKED: { label: "Withdrawn", tone: "danger" },
};

/** Parse one pasted public key. A private key is refused here and again by the cell. */
export function parsePublicKey(text: string): { key?: Record<string, unknown>; problem?: string } {
  let v: unknown;
  try {
    v = JSON.parse(text);
  } catch {
    return { problem: "That is not valid JSON." };
  }
  if (!v || typeof v !== "object" || Array.isArray(v)) return { problem: "Paste one key as a JSON object." };
  const k = v as Record<string, unknown>;
  if (["d", "p", "q", "dp", "dq", "qi", "k"].some((p) => p in k)) return { problem: "That key holds private material. Paste the public key only." };
  if (typeof k.kid !== "string" || !k.kid) return { problem: "The key needs a key id (kid)." };
  return { key: k };
}

export const VERDICT: Record<CredentialVerdict["verdict"], { label: string; tone: "ok" | "warn" | "danger"; text: string }> = {
  IDENTITY_VERIFIED_AUTHORITY_NOT_EVALUATED: { label: "Identity verified", tone: "ok", text: "Zoiko iD vouches for this agent and the presenter holds its key. What the agent may do was not checked: that is decided at the gateway, call by call." },
  VALID_BUT_POSSESSION_NOT_SHOWN: { label: "Valid, holder not shown", tone: "warn", text: "The credential is good, but nobody has shown they hold the agent's key. A copied credential would look the same." },
  NOT_VALID: { label: "Not valid", tone: "danger", text: "Do not rely on this credential." },
};

/** Reason codes in words. Unknown codes are shown as written, never hidden. */
export const REASON_TEXT: Record<string, string> = {
  CREDENTIAL_SIGNATURE_INVALID: "The credential was altered, or Zoiko iD did not sign it.",
  CREDENTIAL_EXPIRED: "The credential has expired.",
  CREDENTIAL_REVOKED: "The credential was withdrawn.",
  CREDENTIAL_UNKNOWN: "Zoiko iD has no record of this credential.",
  AGENT_SUSPENDED: "The agent is stopped.",
  AGENT_REVOKED: "The agent was revoked.",
  AGENT_FROZEN: "The agent is under an emergency freeze.",
  SPONSOR_NOT_ACTIVE: "The agent's sponsor is no longer active.",
  PRESENTATION_SIGNATURE_INVALID: "The presenter does not hold the agent's key.",
  PRESENTATION_AUDIENCE_MISMATCH: "The presentation was made for someone else.",
  PRESENTATION_NONCE_MISMATCH: "The presentation does not answer this check's nonce.",
  PRESENTATION_REPLAYED: "That presentation was already used.",
  PRESENTATION_TOO_OLD: "The presentation is too old.",
};
export const reasonText = (code: string): string => REASON_TEXT[code] ?? code;

/** Which confirmations a mandate still needs, in words. */
export function signingText(s: { required: string[]; missing: string[]; complete: boolean }): string {
  if (s.complete) return "Every confirmation this mandate needs is in place for its exact current terms.";
  const who = s.missing.map((m) => (m === "GRANTOR" ? "the person who handed on their authority" : "an independent second person")).join(" and ");
  return `Still needed: ${who}.`;
}

export const MANDATE_ID = /^[0-9a-f-]{36}$/i;
export const isMandateId = (v: string): boolean => MANDATE_ID.test(v.trim());

export const RISK_TEXT = "These are places to look. They are not findings about an agent, and none of them changes what an agent may do. No signals is not proof that nothing is wrong.";
