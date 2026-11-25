import type { ExportRequest } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { CLAIMS, exportFormProblems, exportNextStep, snapshotHeadline, snapshotReason, trustSummary } from "./wf08-logic";

const RID = "0b7d3f2e-1c4a-4e5b-9a8c-123456789abc";
const form = { tier: "AUDITOR_DOSSIER" as const, receiptId: RID, recipientName: "Big Four LLP", recipientKind: "AUDITOR", contact: "a@b.test", purpose: "Annual audit of access decisions", legalBasis: "AUDIT_ENGAGEMENT", country: "GB", retention: 90 };

const req = (over: Partial<ExportRequest>): ExportRequest => ({
  request_id: "r1", receipt_id: RID, tier: "AUDITOR_DOSSIER", recipient: { name: "x", kind: "AUDITOR" }, purpose: "p", legal_basis: "AUDIT_ENGAGEMENT", retention_days: 30, recipient_country: "GB",
  cross_border: false, needs_second_person: true, state: "REQUESTED", requested_by: "me", requested_at: "2026-11-25T10:00:00Z", decided_by: null, decision_reason: null, issued_at: null, expires_at: null, package_digest: null, ...over,
});

describe("the export request form", () => {
  it("asks a private tier for everything and the public tier for nothing but the receipt", () => {
    expect(exportFormProblems(form)).toEqual([]);
    expect(exportFormProblems({ ...form, tier: "PUBLIC_MINIMAL", recipientName: "", contact: "", purpose: "", legalBasis: "", country: "", retention: 30 })).toEqual([]);
    expect(exportFormProblems({ ...form, recipientName: " " })).toContain("Name the recipient.");
    expect(exportFormProblems({ ...form, purpose: "audit" })).toContain("Say why, in at least a sentence.");
    expect(exportFormProblems({ ...form, legalBasis: "" })).toContain("Choose the legal basis.");
    expect(exportFormProblems({ ...form, country: "Britain" })).toContain("Give the recipient's country as two letters.");
    expect(exportFormProblems({ ...form, retention: 400 })[0]).toContain("between 1 and 365");
    expect(exportFormProblems({ ...form, receiptId: "nope" })).toContain("Enter a receipt ID.");
  });
});

describe("what a person may do with a request", () => {
  it("never lets the person who asked decide a request that needs a second person", () => {
    expect(exportNextStep(req({}), "me")).toMatchObject({ approve: false, issue: false });
    expect(exportNextStep(req({}), "someone-else")).toMatchObject({ approve: true, issue: false });
    expect(exportNextStep(req({ state: "APPROVED" }), "me")).toMatchObject({ approve: false, issue: true });
    expect(exportNextStep(req({ needs_second_person: false }), "me")).toMatchObject({ issue: true });
    expect(exportNextStep(req({ state: "ISSUED" }), "x")).toMatchObject({ approve: false, issue: false });
    expect(exportNextStep(req({ state: "REFUSED" }), "x")).toMatchObject({ approve: false, issue: false });
  });
});

describe("the authority history page", () => {
  it("reads a missing or contradictory link as not shown, never as fine", () => {
    expect(snapshotHeadline({ verdict: "COMPLETE", integrity: "VALID" }).tone).toBe("ok");
    expect(snapshotHeadline({ verdict: "INDETERMINATE", integrity: "VALID" }).tone).toBe("warn");
    expect(snapshotHeadline({ verdict: "COMPLETE", integrity: "INVALID" }).tone).toBe("danger");
    expect(snapshotReason("MISSING_EDGE:GRANT")).toBe("A required link in the chain is missing (GRANT).");
    expect(snapshotReason("SOMETHING_NEW")).toBe("SOMETHING_NEW.");
  });
});

describe("the trust inspector", () => {
  const bundle = { schema: "s", issuer: "i", environment: "production", version: 3, issued_at: "2026-11-25T10:00:00Z", expires_at: "2026-11-26T10:00:00Z", pq_hybrid: "NOT_OFFERED", limits: "", keys: [{ kid: "k1", state: "COMPROMISED" as const, compromised_from: "2026-11-20T00:00:00Z" }, { kid: "k2", state: "ACTIVE" as const }] };
  it("says an expired bundle must not be trusted and what a compromised key voids", () => {
    expect(trustSummary(bundle, new Date("2026-11-25T12:00:00Z")).current).toBe(true);
    const late = trustSummary(bundle, new Date("2026-11-27T00:00:00Z"));
    expect(late.current).toBe(false);
    expect(late.note).toContain("Do not trust");
    expect(late.keys[0]?.standing).toContain("void");
    expect(late.keys[1]?.standing).toBe("active");
  });
});

describe("the claims register", () => {
  it("never allows the claims the evidence does not back", () => {
    const by = (text: string) => CLAIMS.find((c) => c.claim.startsWith(text))?.allowed;
    expect(by("Independently verifiable")).toBe("not yet");
    expect(by("A signed decision proves")).toBe("no");
    expect(by("Post-quantum")).toBe("no");
    expect(by("Qualified")).toBe("no");
  });
});
