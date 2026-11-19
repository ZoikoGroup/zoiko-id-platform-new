"use client";

import { ApiError } from "@zoiko/api-client";
import { Badge, Banner, KeyValues } from "@zoiko/ui";
import type { ReactNode } from "react";

import { ApiErrorView } from "@/lib/op";

/** Query keys for the regulatory screens (ZID-RCG-003 WF-01..08). */
export const REG = {
  readiness: ["regulatory", "readiness"],
  countryOpenings: ["regulatory", "country-openings"],
  faceLab: ["regulatory", "face-lab"],
  packs: ["regulatory", "packs"],
  releases: ["regulatory", "releases"],
  attestations: ["regulatory", "attestations"],
  entitlements: ["regulatory", "entitlements"],
  suspensions: ["regulatory", "suspensions"],
  issuers: ["regulatory", "issuers"],
  claims: ["regulatory", "claims"],
  matrix: ["regulatory", "conformance-matrix"],
  runs: ["regulatory", "conformance-runs"],
  overlays: ["regulatory", "privacy-overlays"],
  biometricMethods: ["regulatory", "biometric-methods"],
  checkMethods: ["regulatory", "check-methods"],
} as const;

/** What a refusal from the regulatory API means, in words an operator can act on. Unknown codes fall back to the server's message. */
export const REG_ERROR_HELP: Record<string, string> = {
  FACE_ENGINE_NOT_CONFIGURED: "The face engine is not set up in this deployment (ZID_FACE_URL).",
  FACE_ENGINE_UNAVAILABLE: "The face engine did not answer. Check that the face service is running, then try again.",
  NO_FACE_PROBE: "No face was found in the live photograph. Use a clear, front-facing photograph in good light.",
  NO_FACE_REFERENCE: "No face was found in the document photograph. Photograph the page with the portrait, flat and sharp.",
  MULTIPLE_FACES_PROBE: "More than one face was found in the live photograph. Only one person may be in it.",
  MULTIPLE_FACES_REFERENCE: "More than one face was found in the document photograph.",
  IMAGE_UNREADABLE: "That photograph could not be read. Use a JPEG or PNG.",
  IMAGE_TYPE_NOT_ALLOWED: "Only JPEG and PNG photographs are accepted.",
  IMAGE_TOO_LARGE: "That photograph is too large (8 MB at most).",
  COUNTRY_OPENING_INCOMPLETE: "Opening a country needs a date, a home cell and a reference for each of the six approvals.",
  COUNTRY_NOT_LISTED: "This country is not in the registry file. Adding a new country is a configuration change, not done here.",
  CELL_UNKNOWN: "This deployment does not know that cell. Pick one from the list.",
  PACK_DRAFT_OPEN: "A draft of this pack is already open. Finish or revise that draft first.",
  PACK_APPROVAL_HASH_MISMATCH: "The pack changed after you looked at it. Reload and sign the hash shown now.",
  PACK_APPROVER_IS_AUTHOR: "The author of a pack cannot approve it.",
  RELEASE_DRAFT_OPEN: "A draft of this release is already open.",
  RELEASE_APPROVAL_HASH_MISMATCH: "The release changed after you looked at it. Reload and sign the hash shown now.",
  RELEASE_APPROVER_IS_AUTHOR: "The author of a release cannot approve it.",
  RELEASE_APPROVER_NOT_INDEPENDENT: "You recorded an evidence gate on this release, so you cannot also approve it.",
  RELEASE_APPROVER_NOT_DISTINCT: "Each approval must come from a different person.",
  RELEASE_RECORDER_IS_AUTHOR: "The author of a release cannot record its evidence gates.",
  RELEASE_NOT_FREEZABLE: "Every evidence gate must pass and an approved policy pack must exist before the release can be frozen.",
  RELEASE_NOT_RELEASEABLE: "All four approvals are needed before launch.",
  CONFORMANCE_REVIEWER_NOT_INDEPENDENT: "The person who recorded a run cannot review it. Ask a different reviewer.",
  CONFORMANCE_ALREADY_REVIEWED: "You have already reviewed this run. One review per reviewer.",
  CONFORMANCE_HASH_MISMATCH: "The run changed or the hash is wrong. Sign the run hash shown.",
  ISSUER_ID_INVALID: "An issuer ID is lowercase letters, digits and hyphens, starting with a letter or digit (for example uni-of-example).",
  ISSUER_RECORD_INVALID: "The issuer record could not be read: check every name has text, every accreditation has a body and a from date, and dates are real dates.",
  ISSUER_SOURCE_URL_INVALID: "Name the official source by its https address.",
  ISSUER_PROOF_REQUIRED: "Give the SHA-256 (64 hex characters) of what the official source returned.",
  ISSUER_EVIDENCE_REQUIRED: "Say where the preserved copy of the source's response is kept (letters, digits and . _ : / # @ + = -, at least 3 characters).",
  ISSUER_AUTHORITY_UNRECORDED: "An issuer trusted for any claim type needs OFFICIAL authority from a recorded legal or regulatory basis. Set authority to OFFICIAL or remove the claim types.",
  ISSUER_CORRECTION_REASON_REQUIRED: "A correction needs a reason of at least 10 characters saying what was wrong.",
  ISSUER_STATEMENT_RETROACTIVE:
    "This changes what an earlier statement said about the past (an accreditation or name removed or shortened, or a revocation moved or backdated). Keep the earlier facts and end them from today, or record it as a CORRECTION with a reason.",
  ISSUER_NOTHING_TO_CORRECT: "Nothing an earlier statement said about the past changes, so this is not a correction. Record it as an UPDATE (the first statement about an issuer is always an UPDATE).",
  ISSUER_OUT_OF_ORDER: "Another statement about this issuer was recorded while you were writing. Reload its history and record yours on top of the latest one.",
  ISSUER_FROM_THE_FUTURE: "The server clock refused the statement time. Try again in a moment.",
  EXPORT_PURPOSE_REQUIRED: "An export needs a stated purpose and a legal basis reference.",
  SUSPENSION_NOT_ACTIVE: "This hold is already lifted.",
};

export function RegError({ error }: { error: unknown }) {
  if (error instanceof ApiError && REG_ERROR_HELP[error.code]) {
    return (
      <Banner tone="danger" title="Not done" live="assertive">
        {REG_ERROR_HELP[error.code]} <span className="block text-xs">{error.message}</span>
      </Banner>
    );
  }
  return <ApiErrorView error={error} />;
}

export function Hash({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-ink-subtle">-</span>;
  return (
    <span className="font-mono text-xs text-ink-muted" title={value}>
      {value.slice(0, 12)}…
    </span>
  );
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-xs">{children}</span>;
}

/** Reason codes as a compact list. An empty list says so in words. */
export function Reasons({ reasons, none = "None" }: { reasons: string[]; none?: string }) {
  if (!reasons.length) return <span className="text-ink-subtle">{none}</span>;
  return (
    <ul className="flex flex-wrap gap-1">
      {reasons.map((r) => (
        <li key={r}>
          <Badge tone="warn">{r}</Badge>
        </li>
      ))}
    </ul>
  );
}

export function Verdict({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return <Badge tone={ok ? "ok" : "danger"}>{ok ? yes : no}</Badge>;
}

export function Facts({ items }: { items: [string, ReactNode][] }) {
  return <KeyValues items={items} columns={2} />;
}

/** "a, b\nc" -> ["a", "b", "c"] */
export function list(v: string): string[] {
  return v
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** A datetime-local or date value as an ISO instant (the API wants an aware time). */
export function isoFromLocal(v: string): string {
  return new Date(v).toISOString();
}

export const upper = (v: string) => v.trim().toUpperCase();

/** Undefined for a blank field, so optional query and body fields are left out rather than sent empty. */
export const opt = (v: string) => (v.trim() ? v.trim() : undefined);

/** Choices for the regulatory forms, so an operator picks a value instead of guessing its spelling. */
const choices = (rows: [string, string][], join: (id: string, name: string) => string) =>
  rows.map(([id, name]) => ({ id, label: join(id, name) }));

export const COUNTRY_OPTIONS = choices(
  [
    ["GB", "United Kingdom"], ["IE", "Ireland"], ["DE", "Germany"], ["FR", "France"], ["ES", "Spain"], ["IT", "Italy"],
    ["NL", "Netherlands"], ["BE", "Belgium"], ["PT", "Portugal"], ["SE", "Sweden"], ["PL", "Poland"], ["CH", "Switzerland"],
    ["US", "United States"], ["CA", "Canada"], ["AU", "Australia"], ["NZ", "New Zealand"], ["IN", "India"],
    ["AE", "United Arab Emirates"], ["SG", "Singapore"], ["ZA", "South Africa"], ["BR", "Brazil"], ["MX", "Mexico"],
  ],
  (id, name) => `${id} - ${name}`,
);

export const CAPABILITY_OPTIONS = choices(
  [
    ["INDIVIDUAL_VERIFICATION", "Verify an individual"],
    ["BUSINESS_VERIFICATION", "Verify a business"],
    ["CREDENTIAL_ISSUANCE", "Issue a credential"],
    ["REGISTRATION", "Registration"],
    ["DISCOVERY", "Discovery"],
    ["ENTERPRISE_USE", "Enterprise use"],
  ],
  (id, name) => `${id} (${name})`,
);

export const PURPOSE_OPTIONS = [{ id: "STANDARD", label: "STANDARD (the default purpose)" }];

export const SCHEME_OPTIONS = choices(
  [
    ["UK_DIATF", "UK digital identity trust framework"],
    ["UK_DVS_REGISTER", "UK digital verification services register"],
    ["EU_TRUSTED_LIST", "EU trusted list"],
    ["PROFESSIONAL_REGISTER", "A professional register"],
    ["OFFICIAL_REGISTER", "An official government register"],
  ],
  (id, name) => `${id} (${name})`,
);
