import type { TrustPackage, TrustPackageState } from "@zoiko/api-client";

export const TRUST_KEY = ["trust-store"];

export type Tone = "neutral" | "ok" | "warn" | "danger" | "info";

/** What each state means, in words. Only ACTIVE is trusted by a chip check; every other state is not. */
export const STATE: Record<TrustPackageState, { label: string; tone: Tone; meaning: string }> = {
  STAGED: { label: "Staged", tone: "neutral", meaning: "Imported with its source. Nothing has been read from it yet and nothing is trusted." },
  VALIDATED: { label: "Validated", tone: "info", meaning: "The list verified against the anchors you confirmed. It is a candidate: a different person must approve it." },
  REJECTED: { label: "Rejected", tone: "danger", meaning: "It did not verify, or a person rejected it. It can never become trusted." },
  APPROVED: { label: "Approved", tone: "info", meaning: "A different person approved it. It is still not trusted until someone other than the stager activates it." },
  ACTIVE: { label: "Active", tone: "ok", meaning: "This is the trust a chip check uses for your organization." },
  SUPERSEDED: { label: "Superseded", tone: "neutral", meaning: "Another package became active. This one can be restored by a roll back." },
  ROLLED_BACK: { label: "Rolled back", tone: "warn", meaning: "It was active and was rolled back with a reason. It is never restored." },
  RETIRED: { label: "Retired", tone: "warn", meaning: "It was active and was retired. Nothing is trusted until another package is activated." },
};

const PROBLEMS: Record<string, string> = {
  ANCHOR_FINGERPRINT_MISMATCH: "The fingerprints you typed are not the fingerprints of the anchor files. Compare them with the country's own publication and type them again.",
  ANCHOR_UNREADABLE: "An anchor file is not a readable certificate.",
  ANCHOR_COUNT_INVALID: "Supply between one and five anchor certificates.",
  PACKAGE_SIZE_INVALID: "The master list is empty or too large.",
  SELF_APPROVAL: "You staged this package, so someone else approves it.",
  SELF_ACTIVATION: "You staged this package, so someone else activates it.",
  INVALID_STATE: "This step is not available for the package in its current state. Reload and look again.",
  REASON_REQUIRED: "Give a reason of at least a few words.",
  NOTHING_TO_ROLL_BACK: "There is no active package to roll back.",
  NO_PREVIOUS_PACKAGE: "No earlier package was ever active, so there is nothing to restore.",
  ROLLBACK_TARGET_INVALID: "Only a package that was active before and was superseded can be restored.",
  NOTHING_TO_RETIRE: "There is no active package to retire.",
  PACKAGE_NOT_FOUND: "That package does not exist for your organization.",
};

/** A refusal code in words. An unknown code shows as itself. */
export function problemText(code: string): string {
  return PROBLEMS[code.toUpperCase()] ?? code;
}

export type Step = "validate" | "approve" | "reject" | "activate";

/**
 * What this person may do with this package. The platform refuses the same things, but the screen says why before it has to: the person who
 * staged a package never approves or activates it, and a step that does not fit the state is not offered.
 */
export function stepsFor(pkg: Pick<TrustPackage, "state" | "staged_by">, me: string | undefined): { steps: Step[]; blockedBecauseYouStaged: boolean } {
  const mine = Boolean(me) && me === pkg.staged_by;
  const steps: Step[] = [];
  let blocked = false;
  if (pkg.state === "STAGED") steps.push("validate", "reject");
  if (pkg.state === "VALIDATED") {
    if (mine) blocked = true;
    else steps.push("approve");
    steps.push("reject");
  }
  if (pkg.state === "APPROVED") {
    if (mine) blocked = true;
    else steps.push("activate");
  }
  return { steps, blockedBecauseYouStaged: blocked };
}

/** A certificate as the platform fingerprints it: SHA-256 of its DER bytes. A PEM file is its base64 body; anything else is taken as DER. */
export function derOf(bytes: Uint8Array): Uint8Array {
  const text = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  if (!text.includes("BEGIN CERTIFICATE")) return bytes;
  const body = text.replace(/-----BEGIN CERTIFICATE-----/, "").replace(/-----END CERTIFICATE-----[\s\S]*$/, "").replace(/\s+/g, "");
  const raw = atob(body);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export async function anchorFingerprint(bytes: Uint8Array): Promise<string> {
  const der = derOf(bytes);
  const digest = await crypto.subtle.digest("SHA-256", der as BufferSource);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** True when what the person typed is the fingerprint of the anchor file (spaces, colons and case do not matter). */
export function fingerprintsMatch(typed: string, computed: string): boolean {
  const clean = (s: string) => s.replace(/[\s:]/g, "").toLowerCase();
  return clean(typed).length === 64 && clean(typed) === clean(computed);
}

export function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}
