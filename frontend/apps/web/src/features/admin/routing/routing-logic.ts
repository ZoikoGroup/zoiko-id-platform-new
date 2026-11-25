import type { RouteAttemptView, RouteDataFlow, RouteDecisionView, RouteHop, RoutePreflight, RouteRelease, RoutingCapability, RoutingCoverage } from "@zoiko/api-client";

/** Plain-language rules for the coverage and routing screens (ZID-CP-WF09 v2.0, CR-171). A selected provider is not a verified identity, and nothing here says otherwise. */

export const GATES: Record<string, string> = {
  G1_CONTEXT: "The session could not be verified",
  G2_WORKFLOW_PURPOSE: "The workflow, purpose or method is not approved for this company",
  G3_MARKET_LEGALITY: "The market is not released",
  G4_DATA_PATH: "Where the data would go is not approved",
  G5_METHOD_ASSURANCE: "The method or assurance is not enough",
  G6_QUALIFICATION_CONTRACT: "The provider is not qualified or contracted for this",
  G7_DEVICE_SDK: "The device or app version is not supported",
  G8_CAPACITY_HEALTH: "The provider has no capacity or is not healthy",
};

const REASONS: Record<string, string> = {
  CONTEXT_NOT_VERIFIED: "The session's signed context did not verify.",
  WORKFLOW_NOT_APPROVED: "The workflow version is not approved.",
  METHOD_NOT_ENTITLED_FOR_TENANT: "This company is not entitled to that method.",
  MARKET_NOT_RELEASED: "No launched, current release covers this country, product and purpose.",
  PROCESSING_REGION_NOT_ALLOWED: "The provider would process data outside the allowed regions.",
  SUBPROCESSOR_REGION_NOT_ALLOWED: "One of the provider's own suppliers would process data outside the allowed regions.",
  NO_LEGAL_DETERMINATION: "No signed legal determination covers the provider's whole data path.",
  LEGAL_DETERMINATION_EXPIRED: "The legal determination has expired.",
  METHOD_NOT_OFFERED: "The provider does not offer this method.",
  CHIP_REQUIRED_BUT_METHOD_IS_WEAKER: "A chip read is required and this method is weaker. A document check never stands in for it.",
  ASSURANCE_BELOW_FLOOR: "The provider's assurance is below what is required.",
  ISSUER_COUNTRY_NOT_COVERED: "The provider does not cover the issuing country.",
  PROVIDER_NOT_APPROVED: "The provider is not approved yet.",
  PROVIDER_SUSPENDED: "The provider is suspended.",
  PROVIDER_EXPIRED: "The provider's approval has expired.",
  PROVIDER_RETIRED: "The provider is retired.",
  QUALIFICATION_EXPIRED: "The provider's qualification has expired.",
  CONTRACT_EXPIRED: "The provider's contract has expired or is not recorded.",
  TENANT_NOT_ENTITLED_TO_PROVIDER: "This company may not use this provider.",
  CHANNEL_NOT_SUPPORTED: "The provider does not support this channel.",
  SDK_VERSION_NOT_SUPPORTED: "The app's SDK version is too old, or was not reported.",
  PROVIDER_HEALTH_DOWN: "The provider is down.",
  PROVIDER_HEALTH_UNKNOWN: "The provider's health is not known, so it is not treated as healthy.",
  QUOTA_EXHAUSTED: "The provider's contracted quota is used up.",
  NO_PROVIDER_REGISTERED: "No provider is registered for this.",
  NO_DATA_FLOW_GRAPH: "No data-flow graph is registered for this provider, so where its data goes is not known.",
  DATA_FLOW_CHANGED_SINCE_DETERMINATION: "The provider's data flow changed after the legal determination was made. It is not used until legal covers the new graph.",
  DATA_FLOW_EMPTY: "The data-flow graph is empty.",
  DATA_FLOW_MISSING_INGRESS: "The data-flow graph does not say where data enters.",
  DATA_FLOW_MISSING_STORAGE: "The data-flow graph does not say where data is stored.",
  BREAKER_OPEN: "The provider is failing too often in recent measured attempts, so it is paused.",
  QUALITY_QUARANTINED: "Too few of this method's results are valid, so the method is paused even though the provider is up.",
  PROVIDER_THROTTLED: "The provider's per-minute limit is reached, so a burst does not overload it.",
  "ENTITLEMENT:NO_ENTITLEMENT": "This company has no entitlement for this provider and method.",
  "ENTITLEMENT:REGION_NOT_PERMITTED": "This company's entitlement does not allow the provider's processing region.",
  "ENTITLEMENT:COUNTRY_NOT_COVERED": "This company's entitlement does not cover the country.",
  "ENTITLEMENT:PURPOSE_NOT_PERMITTED": "This company's entitlement does not cover the purpose.",
  "ENTITLEMENT:ENTITLEMENT_NOT_ACTIVE": "This company's entitlement is suspended or revoked.",
  "ENTITLEMENT:ENTITLEMENT_NOT_IN_PERIOD": "This company's entitlement is outside its dates.",
  "ENTITLEMENT:ASSURANCE_TOO_LOW": "This company's entitlement is for a lower assurance than required.",
  PROVIDER_NO_LONGER_REGISTERED: "The provider is no longer registered.",
};
export const reasonLine = (code: string): string => {
  if (REASONS[code]) return REASONS[code];
  if (code.startsWith("DATA_FLOW_REGION_NOT_ALLOWED:")) return `A ${code.slice(29).toLowerCase().replaceAll("_", " ")} step of the provider's data flow is outside the allowed regions.`;
  return code;
};

/** One headline for a decision. A missing route is never an applicant failure. */
export function decisionHeadline(d: Pick<RouteDecisionView, "status" | "selected" | "no_route_reasons">): { tone: "info" | "warn"; text: string } {
  if (d.status === "ROUTE_SELECTED") return { tone: "info", text: `${d.selected} was chosen. That is a routing choice, not a verified identity.` };
  return { tone: "warn", text: "No route is available for this request. That is not a failed applicant; the reasons below say what is missing." };
}

export function rejectionLines(d: Pick<RouteDecisionView, "rejected">): { provider: string; gates: string[]; reasons: string[] }[] {
  return d.rejected.map((r) => ({ provider: r.provider_id, gates: r.gates.map((g) => GATES[g] ?? g), reasons: r.reasons.map(reasonLine) }));
}

const TIER_ORDER = ["ENABLED", "QUALIFIED", "CONTRACTED", "LABORATORY", "DEGRADED", "RESTRICTED"];
const TIER_WORDS: Record<string, string> = {
  ENABLED: "Enabled: launched, approved and current",
  QUALIFIED: "Qualified: approved, but the market is not launched",
  CONTRACTED: "Contracted: a contract exists, nothing more is proven",
  LABORATORY: "Laboratory: tested only",
  DEGRADED: "Degraded: working below expectation",
  RESTRICTED: "Restricted: suspended or its evidence has expired",
};
export function coverageRows(c: RoutingCoverage): { tier: string; words: string; countries: number; specimens: number }[] {
  return TIER_ORDER.filter((t) => c.tiers[t]).map((t) => ({ tier: t, words: TIER_WORDS[t] ?? t, countries: c.tiers[t]?.countries ?? 0, specimens: c.tiers[t]?.specimens ?? 0 }));
}

/** What a capability is still missing before a second person can approve it, in plain words. */
export function capabilityGaps(c: RoutingCapability, now: Date): string[] {
  const out: string[] = [];
  if (!c.qualified_until) out.push("No qualification date.");
  else if (new Date(c.qualified_until) <= now) out.push("Qualification has expired.");
  if (!c.contract_until) out.push("No contract date.");
  else if (new Date(c.contract_until) <= now) out.push("The contract has expired.");
  if (!c.legal_determination_ref) out.push("No signed legal determination of the data path.");
  if (c.observed && c.observed.measured === false) out.push("Speed and success rate are the vendor's own claim, not a measurement.");
  return out;
}

export function capabilityNext(c: RoutingCapability, me: string | null): { approve: boolean; why: string } {
  if (c.state === "APPROVED") return { approve: false, why: "Approved." };
  if (c.state === "RETIRED") return { approve: false, why: "Retired." };
  if (me !== null && c.proposed_by === me) return { approve: false, why: "A second person must approve. You proposed this one." };
  return { approve: true, why: "Waiting for a second person." };
}

export function dataPathLine(c: Pick<RoutingCapability, "regions">, allowed: string[]): { ok: boolean; text: string } {
  const regions = c.regions ?? [];
  const outside = regions.filter((r) => !allowed.includes(r));
  return outside.length ? { ok: false, text: `Processes in ${outside.join(", ")}, which is not allowed.` } : { ok: true, text: `Processes in ${regions.join(", ") || "no region recorded"}.` };
}

const STATE_HELP: Record<string, string> = {
  ATTEMPT_RESERVED: "Reserved. Nothing has been sent to the provider.",
  DISPATCHED: "Sent to the provider. Waiting.",
  PENDING: "The provider has not answered yet.",
  RESULT_RECEIVED: "The provider answered. That is evidence, not a decision.",
  OUTCOME_UNKNOWN: "We cannot tell whether the provider processed it. It must be reconciled, never sent to another provider.",
  RECONCILING: "Asking the provider what happened.",
  FAILED_BEFORE_DISPATCH: "Nothing was sent. Another provider may be asked.",
  CONFIRMED_NOT_PROCESSED: "The provider confirmed it did not process this. Another provider may be asked.",
  CANCELLED: "Cancelled before anything was sent.",
  NORMALIZED: "The result is in a standard form. The owning verifier still decides.",
  REVIEW_REQUIRED: "A person must review it.",
  EVIDENCE_REJECTED: "The evidence was refused.",
  EXCEPTION_REVIEW: "Waiting for an exception review.",
  EXPLICIT_RECAPTURE_APPROVED: "The applicant will capture again.",
  FINALIZED: "Decided by the owning verifier.",
};
export const attemptHelp = (state: string): string => STATE_HELP[state] ?? state.toLowerCase().replaceAll("_", " ");

/** The buttons an operator may press now. Finalising is never one of them: only the owning verifier does that. */
export function attemptActions(a: Pick<RouteAttemptView, "state" | "terminal">): { target: string; label: string }[] {
  if (a.terminal) return [];
  switch (a.state) {
    case "OUTCOME_UNKNOWN":
      return [
        { target: "RECONCILING", label: "Start reconciling" },
        { target: "EXCEPTION_REVIEW", label: "Send to exception review" },
      ];
    case "RECONCILING":
      return [
        { target: "CONFIRMED_NOT_PROCESSED", label: "Provider confirmed not processed" },
        { target: "EXCEPTION_REVIEW", label: "Send to exception review" },
      ];
    case "RESULT_RECEIVED":
      return [{ target: "REVIEW_REQUIRED", label: "Send to review" }];
    default:
      return [];
  }
}

export function preflightHeadline(p: Pick<RoutePreflight, "passes" | "blocking" | "rows">): { tone: "ok" | "warn" | "danger"; text: string } {
  if (!p.passes) return { tone: "danger", text: `${p.blocking} required ${p.blocking === 1 ? "group loses" : "groups lose"} coverage. This cannot go live without an approved exception.` };
  const switched = p.rows.filter((r) => r.change === "SWITCHES_PROVIDER").length;
  const lost = p.rows.filter((r) => r.change === "LOSES_COVERAGE").length;
  if (lost || switched) return { tone: "warn", text: `${lost} not required ${lost === 1 ? "group loses" : "groups lose"} coverage and ${switched} switch provider. A switch may need the applicant to capture again.` };
  return { tone: "ok", text: "No group changes. This is a projection from registered data; it called no provider." };
}

export function releaseNext(r: RouteRelease, me: string | null): { approve: boolean; publish: boolean; why: string } {
  const signed = new Set(r.approvals.map((a) => a.role));
  const missing = r.required_approvers.filter((x) => !signed.has(x));
  if (r.state === "DRAFT") return { approve: false, publish: false, why: "Run the preflight first. A release is approved only after it passes." };
  if (r.state === "LIVE") return { approve: false, publish: false, why: "Live." };
  if (r.state === "ROLLED_BACK") return { approve: false, publish: false, why: "Rolled back." };
  const mine = me !== null && r.maker === me;
  if (missing.length) return { approve: !mine, publish: false, why: mine ? "You made this release, so you cannot approve it." : `Still needs: ${missing.join(", ").toLowerCase()}.` };
  return { approve: false, publish: !mine, why: mine ? "You made this release, so someone else must publish it." : "Every required approval is in. Ready to publish." };
}

export const CLAIM_GUARD = "A public coverage number comes only from launched, approved, current cells. If any cell lacks approved evidence the number is refused, not lowered.";

const HOP_KINDS = ["INGRESS", "TRANSIT", "STORAGE", "REMOTE_SUPPORT", "ANALYTICS", "TELEMETRY", "BACKUP", "SUBPROCESSOR"];
const DATA_CLASSES = ["MEDIA", "TEMPLATE", "DERIVED", "METADATA"];

/** One hop per line as: kind, party, region, data class. Returns what could not be read and never guesses. */
export function parseHops(text: string): { hops: RouteHop[]; problems: string[] } {
  const hops: RouteHop[] = [];
  const problems: string[] = [];
  text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .forEach((line, i) => {
      const [kind, party, region, data_class] = line.split(",").map((x) => x.trim());
      if (!kind || !HOP_KINDS.includes(kind.toUpperCase())) problems.push(`Line ${i + 1}: the first word must be one of ${HOP_KINDS.join(", ").toLowerCase()}.`);
      else if (!party) problems.push(`Line ${i + 1}: name the party.`);
      else if (!region || !/^[a-z0-9][a-z0-9_.-]{1,62}$/.test(region)) problems.push(`Line ${i + 1}: the region is a short lower-case name such as uk.`);
      else if (!data_class || !DATA_CLASSES.includes(data_class.toUpperCase())) problems.push(`Line ${i + 1}: the data class must be media, template, derived or metadata.`);
      else hops.push({ kind: kind.toUpperCase(), party, region, data_class: data_class.toUpperCase() });
    });
  if (!hops.length && !problems.length) problems.push("Add at least one line.");
  const kinds = new Set(hops.map((h) => h.kind));
  if (hops.length && !kinds.has("INGRESS")) problems.push("Say where data enters (an ingress line).");
  if (hops.length && !kinds.has("STORAGE")) problems.push("Say where data is stored (a storage line).");
  return { hops, problems };
}

/** Hops outside the regions the company allows, so a reader sees the blocker before routing does. */
export function hopsOutside(flow: Pick<RouteDataFlow, "hops">, allowed: string[]): RouteHop[] {
  return flow.hops.filter((h) => !allowed.includes(h.region));
}

export function evidenceLine(e: { event_type: string; outcome: string; evidence_class: string }): string {
  return `${e.event_type} (${e.outcome.toLowerCase().replaceAll("_", " ")}, evidence class ${e.evidence_class})`;
}
