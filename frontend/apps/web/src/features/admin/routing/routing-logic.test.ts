import type { RouteRelease, RoutingCapability } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { attemptActions, attemptHelp, capabilityGaps, capabilityNext, coverageRows, dataPathLine, decisionHeadline, evidenceLine, hopsOutside, parseHops, preflightHeadline, reasonLine, rejectionLines, releaseNext } from "./routing-logic";

const NOW = new Date("2026-11-25T10:00:00Z");
const cap = (over: Partial<RoutingCapability> = {}): RoutingCapability => ({
  provider_id: "p", method: "DOCUMENT_FACE", version: 1, state: "UNDER_REVIEW", manifest_digest: "d", product: "idv", countries: ["GB"], channels: ["web"], regions: ["uk"], assurance: 3,
  qualified_until: "2027-06-01T00:00:00Z", contract_until: "2027-06-01T00:00:00Z", legal_determination_ref: "LD-1", proposed_by: "me", approved_by: null, reason: null, observed: { measured: true }, health: "UP", ...over,
});
const rel = (over: Partial<RouteRelease> = {}): RouteRelease => ({ release_id: "r", version: 2, state: "PREFLIGHT_PASSED", maker: "maker", risk: "HIGH", dependency_digest: "d", scope: {}, required_approvers: ["PRODUCT", "SECURITY", "PRIVACY", "LEGAL"], recorded_at: "2026-11-25T10:00:00Z", approvals: [], ...over });

describe("decisions", () => {
  it("never reads a missing route as a failed applicant and never reads a chosen provider as a verified identity", () => {
    expect(decisionHeadline({ status: "NO_ROUTE", selected: null, no_route_reasons: ["MARKET_NOT_RELEASED"] }).text).toContain("not a failed applicant");
    expect(decisionHeadline({ status: "ROUTE_SELECTED", selected: "prov-a", no_route_reasons: [] }).text).toContain("not a verified identity");
    expect(reasonLine("CHIP_REQUIRED_BUT_METHOD_IS_WEAKER")).toContain("never stands in");
    expect(reasonLine("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(rejectionLines({ rejected: [{ provider_id: "x", gates: ["G4_DATA_PATH"], reasons: ["NO_LEGAL_DETERMINATION"] }] })[0]).toEqual({ provider: "x", gates: ["Where the data would go is not approved"], reasons: ["No signed legal determination covers the provider's whole data path."] });
  });
});

describe("coverage", () => {
  it("lists tiers in order of what is proven and says what each means", () => {
    const rows = coverageRows({ as_of: "x", method: null, unknown_cells: 1, tiers: { RESTRICTED: { countries: 1, specimens: 2 }, ENABLED: { countries: 3, specimens: 9 }, LABORATORY: { countries: 5, specimens: 5 } } });
    expect(rows.map((r) => r.tier)).toEqual(["ENABLED", "LABORATORY", "RESTRICTED"]);
    expect(rows[0]?.words).toContain("launched, approved and current");
  });
});

describe("providers", () => {
  it("names every gap before approval and keeps a vendor claim apart from a measurement", () => {
    expect(capabilityGaps(cap(), NOW)).toEqual([]);
    const bad = capabilityGaps(cap({ qualified_until: null, contract_until: "2026-01-01T00:00:00Z", legal_determination_ref: null, observed: { measured: false } }), NOW);
    expect(bad).toEqual(["No qualification date.", "The contract has expired.", "No signed legal determination of the data path.", "Speed and success rate are the vendor's own claim, not a measurement."]);
  });
  it("lets only a second person approve", () => {
    expect(capabilityNext(cap(), "me").approve).toBe(false);
    expect(capabilityNext(cap(), "other").approve).toBe(true);
    expect(capabilityNext(cap({ state: "APPROVED" }), "other").approve).toBe(false);
  });
  it("flags a data path outside the allowed regions", () => {
    expect(dataPathLine({ regions: ["uk"] }, ["uk"]).ok).toBe(true);
    expect(dataPathLine({ regions: ["uk", "us"] }, ["uk"])).toEqual({ ok: false, text: "Processes in us, which is not allowed." });
  });
});

describe("attempts", () => {
  it("never offers to finalise, and an unknown outcome can only be reconciled or reviewed", () => {
    expect(attemptActions({ state: "OUTCOME_UNKNOWN", terminal: false }).map((a) => a.target)).toEqual(["RECONCILING", "EXCEPTION_REVIEW"]);
    expect(attemptActions({ state: "NORMALIZED", terminal: false }).map((a) => a.target)).not.toContain("FINALIZED");
    expect(attemptActions({ state: "FINALIZED", terminal: true })).toEqual([]);
    expect(attemptHelp("OUTCOME_UNKNOWN")).toContain("never sent to another provider");
    expect(attemptHelp("RESULT_RECEIVED")).toContain("not a decision");
  });
});

describe("simulation", () => {
  it("blocks on a required group losing coverage and warns that a switch may need a new capture", () => {
    expect(preflightHeadline({ passes: false, blocking: 1, rows: [] }).tone).toBe("danger");
    const warn = preflightHeadline({ passes: true, blocking: 0, rows: [{ scenario: {}, before: "a", after: "b", change: "SWITCHES_PROVIDER", required: false, after_no_route_reasons: [] }] });
    expect(warn.tone).toBe("warn");
    expect(warn.text).toContain("capture again");
    expect(preflightHeadline({ passes: true, blocking: 0, rows: [] }).text).toContain("called no provider");
  });
});

describe("releases", () => {
  it("needs a passed preflight, then every required role, and never lets the maker approve or publish", () => {
    expect(releaseNext(rel({ state: "DRAFT" }), "x")).toMatchObject({ approve: false, publish: false });
    expect(releaseNext(rel(), "maker").approve).toBe(false);
    expect(releaseNext(rel(), "x")).toMatchObject({ approve: true, publish: false });
    const full = rel({ approvals: ["PRODUCT", "SECURITY", "PRIVACY", "LEGAL"].map((role) => ({ approver: role, role })) });
    expect(releaseNext(full, "maker")).toMatchObject({ publish: false });
    expect(releaseNext(full, "x")).toMatchObject({ publish: true });
    expect(releaseNext(rel({ state: "LIVE" }), "x")).toMatchObject({ approve: false, publish: false });
  });
});

describe("data flow", () => {
  it("reads one hop per line and refuses what it cannot read instead of guessing", () => {
    const ok = parseHops("ingress, provider, uk, media\nstorage, provider, uk, media\nanalytics, stats, us, derived");
    expect(ok.problems).toEqual([]);
    expect(ok.hops.map((h) => h.kind)).toEqual(["INGRESS", "STORAGE", "ANALYTICS"]);
    expect(parseHops("").problems).toEqual(["Add at least one line."]);
    expect(parseHops("wire, x, uk, media").problems[0]).toContain("the first word must be one of");
    expect(parseHops("ingress, , uk, media").problems[0]).toBe("Line 1: name the party.");
    expect(parseHops("ingress, p, UK Region, media").problems[0]).toContain("short lower-case name");
    expect(parseHops("ingress, p, uk, secrets").problems[0]).toContain("data class");
    expect(parseHops("ingress, p, uk, media").problems).toEqual(["Say where data is stored (a storage line)."]);
  });
  it("shows the steps outside the allowed regions and explains the blocker in words", () => {
    const flow = { hops: [{ kind: "STORAGE", party: "p", region: "uk", data_class: "MEDIA" }, { kind: "ANALYTICS", party: "s", region: "us", data_class: "DERIVED" }] };
    expect(hopsOutside(flow, ["uk"]).map((h) => h.region)).toEqual(["us"]);
    expect(reasonLine("DATA_FLOW_REGION_NOT_ALLOWED:REMOTE_SUPPORT")).toContain("remote support step");
    expect(reasonLine("DATA_FLOW_CHANGED_SINCE_DETERMINATION")).toContain("until legal covers the new graph");
    expect(reasonLine("BREAKER_OPEN")).toContain("paused");
    expect(reasonLine("QUALITY_QUARANTINED")).toContain("even though the provider is up");
    expect(reasonLine("ENTITLEMENT:NO_ENTITLEMENT")).toContain("no entitlement");
    expect(evidenceLine({ event_type: "route.selected", outcome: "SUCCESS", evidence_class: "E3" })).toBe("route.selected (success, evidence class E3)");
  });
});
