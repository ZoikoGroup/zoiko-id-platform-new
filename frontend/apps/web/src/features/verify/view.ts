/** The public credential verifier (GID-061, section 08 of ZID-GAP-001 v2.0): what a person with no account may be told.
 *
 * The cell already returns only a bounded view (what the claim is, the decision, its tier, when it was checked) and only under a
 * grant the holder shared. This file turns that into plain words and never says more than the tier proves: a signed credential
 * is not a confirmation by the source, and nothing here ever says "verified professional". */

export type PublicView = {
  claim_type?: string;
  decision?: string;
  assurance_tier?: string;
  credential_status?: string;
  source_checked_at?: string;
  issuer_ref?: string | null;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A verifier link is `{tenant}.{check}?grant={grant}`; anything else is refused before a request is made. */
export function parseLink(ref: string, grant: string | undefined): { ref: string; grant: string } | null {
  const [tenant, check, ...rest] = ref.split(".");
  if (rest.length > 0 || !tenant || !check || !UUID.test(tenant) || !UUID.test(check)) return null;
  if (!grant || !UUID.test(grant)) return null;
  return { ref: `${tenant}.${check}`, grant };
}

export const CLAIM_LABEL: Record<string, string> = {
  ACADEMIC_AWARD: "An academic award",
  PROFESSIONAL_LICENSE: "A professional license",
  INDUSTRY_CERTIFICATE: "An industry certificate",
  CPD_RECORD: "A continuing education record",
  EMPLOYMENT_ASSERTION: "An employment record",
  INSURANCE_ASSERTION: "A professional insurance record",
  DISCIPLINARY_FINDING: "A regulator record",
};

export type Outcome = { tone: "confirmed" | "limited" | "not-confirmed" | "unavailable"; headline: string; detail: string };

/** One sentence each, chosen from the decision and the tier together. Never stronger than the evidence. */
export function outcome(view: PublicView): Outcome {
  const decision = view.decision ?? "";
  const tier = view.assurance_tier ?? "";
  if (decision === "NO_LONGER_VALID") {
    return { tone: "not-confirmed", headline: "No longer valid", detail: "This result has been replaced by a newer check. Ask the holder for a current link." };
  }
  if (decision === "VERIFIED" && tier === "PRIMARY_SOURCE_CONFIRMED") {
    return { tone: "confirmed", headline: "Confirmed by the source", detail: "The organization that holds this record confirmed it at the time shown. This does not show anything after that time." };
  }
  if (decision === "VERIFIED") {
    return { tone: "limited", headline: "Checked, not confirmed by the source", detail: "The credential is genuine as issued, but the source itself has not confirmed it." };
  }
  if (decision === "SOURCE_UNAVAILABLE" || decision === "UNVERIFIABLE" || decision === "INDETERMINATE") {
    return { tone: "unavailable", headline: "Could not be confirmed", detail: "This is not a finding about the person. The source could not be reached or cannot confirm it." };
  }
  if (decision === "REVIEW") {
    return { tone: "unavailable", headline: "Waiting for a person to decide", detail: "A reviewer is still looking at this result." };
  }
  return { tone: "not-confirmed", headline: "Not confirmed", detail: "The source did not confirm this claim." };
}

export function checkedOn(iso: string | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
}
