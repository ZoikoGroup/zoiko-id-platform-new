"use client";

import { ApiError } from "@zoiko/api-client";
import { Badge, Banner, KeyValues } from "@zoiko/ui";
import type { ReactNode } from "react";

import { ApiErrorView } from "@/lib/op";

/** Query keys for the regulatory screens (ZID-RCG-003 WF-01..08). */
export const REG = {
  readiness: ["regulatory", "readiness"],
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
} as const;

/** What a refusal from the regulatory API means, in words an operator can act on. Unknown codes fall back to the server's message. */
export const REG_ERROR_HELP: Record<string, string> = {
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
  CONFORMANCE_REVIEWER_IS_RECORDER: "The person who recorded a run cannot review it.",
  CONFORMANCE_HASH_MISMATCH: "The run changed or the hash is wrong. Sign the run hash shown.",
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
