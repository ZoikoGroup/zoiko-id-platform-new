/** How an independent assurance status is shown (ZID-UAT-005). Only a recorded independent PASS reads as done; NOT_RUN, BLOCKED, INCONCLUSIVE and WAIVED never do. */
export type BadgeState = "ACTIVE" | "REJECTED" | "PENDING";

export function badge(status: string): BadgeState {
  if (status === "PASS") return "ACTIVE";
  if (status === "FAIL" || status === "EXPIRED") return "REJECTED";
  return "PENDING";
}

/** A release reads as go only when the server's decision says GO and nothing is missing; a missing item is never shown as ready. */
export function releaseReads(decision: { status: string; missing: string[] }): "go" | "no go" {
  return decision.status === "GO" && decision.missing.length === 0 ? "go" : "no go";
}

export type Bundle = { manifest_id?: string; manifest?: Record<string, unknown>; jws?: string; events?: Record<string, unknown>[] };

/** Read what a person pasted into the evidence builder. A manifest id alone checks the stored manifest; a manifest needs its signature; anything else is refused. */
export function parseBundle(text: string): { ok: true; bundle: Bundle } | { ok: false; error: string } {
  const raw = text.trim();
  if (!raw) return { ok: false, error: "Paste a manifest id or a manifest bundle." };
  if (/^[0-9a-fA-F-]{36}$/.test(raw)) return { ok: true, bundle: { manifest_id: raw } };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, error: "That is neither a manifest id nor valid JSON." };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return { ok: false, error: "A bundle is a JSON object." };
  const b = parsed as Bundle;
  if (b.manifest_id === undefined && (b.manifest === undefined || typeof b.jws !== "string" || !b.jws)) {
    return { ok: false, error: "A bundle needs a manifest id, or a manifest and its signature (jws)." };
  }
  return { ok: true, bundle: b };
}

/** The cautious wording for a verification: only a clean result reads as intact. */
export function verifyWords(r: { valid: boolean; problems: string[]; locked_releases: string[] }): string {
  if (r.valid) return "Intact: the signature, the digest and the chain all hold.";
  const locked = r.locked_releases.length ? ` ${r.locked_releases.length} release(s) built on it are now locked.` : "";
  return `Not intact: ${r.problems.join(", ").toLowerCase().replaceAll("_", " ")}.${locked}`;
}

