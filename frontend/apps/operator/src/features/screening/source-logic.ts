import type { ScreeningFreshness, ScreeningRelease, ScreeningReleaseState } from "@zoiko/api-client";

export type Tone = "neutral" | "ok" | "warn" | "danger" | "info";

export const FRESHNESS: Record<ScreeningFreshness, { tone: Tone; text: string }> = {
  FRESH: { tone: "ok", text: "Fresh" },
  STALE: { tone: "danger", text: "Stale: screening cannot clear anyone against it" },
  NEVER_RELEASED: { tone: "warn", text: "No release yet: screening is unavailable for this source" },
};

export const STATE: Record<ScreeningReleaseState, { tone: Tone; text: string }> = {
  VALIDATED: { tone: "info", text: "Awaiting approval" },
  QUARANTINED: { tone: "danger", text: "Quarantined" },
  ACTIVE: { tone: "ok", text: "In use" },
  SUPERSEDED: { tone: "neutral", text: "Superseded" },
};

const CODES: Record<string, string> = {
  SOURCE_NOT_REGISTERED: "This source is not in the register of official sources.",
  LEGACY_SOURCE_REJECTED: "This is a legacy list that has been closed. The current list replaced it.",
  URL_NOT_HTTPS: "The file was not fetched over https.",
  HOST_NOT_THE_PUBLISHERS: "The address is not one the publisher is recorded as using.",
  URL_HAS_CREDENTIALS: "The address carries a user name or password.",
  SCHEMA_INVALID: "The file did not parse as this list's format (it may be truncated).",
  CHECKSUM_MISMATCH: "The file's digest differs from the one the publisher advertised.",
  PUBLISHED_BEFORE_CURRENT_LIST: "The file was published before the current list began: it is a stale legacy file.",
  ZERO_ROWS: "The file holds no entries.",
  COUNT_DRIFT_ABOVE_LIMIT: "The number of entries moved by more than the approved share since the release in use.",
  PUBLISHER_DIGEST_NOT_AVAILABLE: "The publisher gives no digest to compare.",
  IDENTICAL_TO_ACTIVE: "Identical to the release in use: approving only refreshes it.",
  TWO_APPROVERS_REQUIRED: "Two people other than the one who staged it must approve.",
};

/** A validation, flag or refusal code in words an operator can act on. An unknown code shows as itself. */
export function codeText(code: string): string {
  const flag = /^FLAG_NOT_ACKNOWLEDGED:(.+)$/.exec(code);
  if (flag) return `Not yet acknowledged by every approver: ${codeText(flag[1]!)}`;
  const state = /^STATE_(.+)$/.exec(code);
  if (state) return `This release is ${state[1]!.toLowerCase()}, so it cannot be published.`;
  return CODES[code] ?? code;
}

/** How many distinct people have approved publishing, and how many approved a rollback. They are counted separately: one is not the other. */
export function approvalCounts(r: Pick<ScreeningRelease, "approvals">): { publish: number; rollback: number } {
  const count = (kind: "PUBLISH" | "ROLLBACK") => new Set(r.approvals.filter((a) => a.kind === kind).map((a) => a.approver)).size;
  return { publish: count("PUBLISH"), rollback: count("ROLLBACK") };
}

/** The flags every approver must acknowledge by name: the ones the validation raised. */
export function flagsToAcknowledge(r: Pick<ScreeningRelease, "flags">): string[] {
  return [...r.flags];
}

export function shortDigest(sha: string): string {
  return sha.length > 16 ? `${sha.slice(0, 12)}…` : sha;
}

/** What can be done with a release now, from its state alone. The platform decides again on the server. */
export function actionsFor(r: Pick<ScreeningRelease, "state" | "publishable">): { approve: boolean; publish: boolean; rollback: boolean } {
  return {
    approve: r.state === "VALIDATED",
    publish: r.state === "VALIDATED" && r.publishable.length === 0,
    rollback: r.state === "SUPERSEDED",
  };
}
