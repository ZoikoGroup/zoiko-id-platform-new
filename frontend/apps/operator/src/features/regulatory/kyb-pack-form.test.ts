import { describe, expect, it } from "vitest";

import { buildPack, DAY, defaultForm, gaps, PURPOSE } from "./kyb-pack-form";

// The parts of the pack these tests read, so no `any` is needed.
type Pack = Record<string, unknown> & {
  sources: Record<string, unknown>[];
  ubo: Record<string, unknown>;
  retention: Record<string, unknown> & { days: unknown };
};

describe("the pack form", () => {
  it("builds exactly the pack object the platform reads", () => {
    const f = defaultForm();
    f.jurisdiction = "GB";
    f.sources["gleif"] = { on: true, legalBasis: "LB-1", credentialRef: "", terms: "v1" };
    f.documents["REGISTER_EXTRACT"] = { on: true, ageDays: "90" };
    f.uboSignedBy = "A. Owner";
    f.retentionBasis = "Companies Act";
    f.deletionJob = "job-7";
    const p = buildPack(f) as unknown as Pack;
    expect(p.jurisdiction).toBe("GB");
    expect(p.purpose).toBe(PURPOSE);
    expect(p.region).toBeNull();
    expect(p.sources).toEqual([{ adapter: "gleif", tier: 3, legal_basis: "LB-1", credential_ref: null, credential_required: false, terms_version: "v1" }]);
    expect(p.documents).toContainEqual({ doc_class: "CERTIFICATE_OF_INCORPORATION", claim: "LEGAL_EXISTENCE_HISTORICAL", max_age_s: null }); // an event does not age
    expect(p.documents).toContainEqual({ doc_class: "REGISTER_EXTRACT", claim: "CURRENT_STATUS", max_age_s: 90 * DAY });
    expect(p.ubo).toEqual({ threshold_percent: "25", other_control_tests: true, signed_by: "A. Owner" });
    expect(p.retention.days).toEqual({ RAW_DOCUMENTS: 365, EXTRACTED_DATA: 365, DECISION_METADATA: 365, SCREENING_EVIDENCE: 365 });
    expect(p.recheck_days).toEqual({ K1: 180, K2: 365 });
    expect(p.reviewers_for_document_first).toBe(2);
  });

  it("sends empty text as null so the platform names it as missing, never an invented value", () => {
    const p = buildPack(defaultForm()) as unknown as Pack;
    expect(p.sources[0]?.legal_basis).toBeNull();
    expect(p.ubo.signed_by).toBeNull();
    expect(p.retention.legal_basis).toBeNull();
    expect(p.retention.deletion_job_ref).toBeNull();
  });

  it("lists what is still to fill in, then nothing once it is all there", () => {
    const f = defaultForm();
    expect(gaps(f).length).toBeGreaterThan(3);
    f.sources["gleif"] = { on: true, legalBasis: "LB-1", credentialRef: "", terms: "v1" };
    f.uboSignedBy = "A. Owner";
    f.retentionBasis = "Companies Act";
    f.deletionJob = "job-7";
    expect(gaps(f)).toEqual([]);
  });

  it("asks for a key name only for a source that needs a key", () => {
    const f = defaultForm();
    f.sources["companies-house"] = { on: true, legalBasis: "LB", credentialRef: "", terms: "v1" };
    expect(gaps(f).some((g) => g.includes("Companies House") && g.includes("key"))).toBe(true);
    f.sources["companies-house"]!.credentialRef = "companies-house-api-key";
    expect(gaps(f).some((g) => g.includes("Companies House"))).toBe(false);
  });
});
