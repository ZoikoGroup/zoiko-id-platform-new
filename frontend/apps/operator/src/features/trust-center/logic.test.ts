import { describe, expect, it } from "vitest";

import { blockerText, figure, hoursText, offers, shortHash, STATUS } from "./logic";

const base = { status: "APPROVAL_PENDING", ready_to_publish: false, history: [] as { revision: number; content_hash: string; status: string; created_at: string; published_at: string | null; withdrawn_at: string | null; was_published: boolean }[] };

describe("trust center screen rules", () => {
  it("offers publish only when the server says the revision is ready", () => {
    expect(offers(base).publish).toBe(false);
    expect(offers({ ...base, ready_to_publish: true }).publish).toBe(true);
  });
  it("offers withdraw only for a live claim and never approve or publish for a withdrawn one", () => {
    expect(offers({ ...base, status: "PUBLISHED" })).toMatchObject({ withdraw: true, approve: false, publish: false });
    expect(offers({ ...base, status: "EMERGENCY_WITHDRAWN", ready_to_publish: true })).toMatchObject({ withdraw: false, approve: false, publish: false });
  });
  it("offers a rollback only when an earlier revision went through the gate and was not withdrawn", () => {
    const h = (status: string, was: boolean) => ({ revision: 1, content_hash: "h", status, created_at: "", published_at: null, withdrawn_at: null, was_published: was });
    expect(offers({ ...base, status: "PUBLISHED", history: [h("SUPERSEDED", true)] }).rollback).toBe(true);
    expect(offers({ ...base, status: "PUBLISHED", history: [h("EMERGENCY_WITHDRAWN", true)] }).rollback).toBe(false);
    expect(offers({ ...base, status: "PUBLISHED", history: [h("SUPERSEDED", false)] }).rollback).toBe(false);
  });
  it("never writes a figure for a service with no validated measurement", () => {
    expect(figure(null, "METRIC_UNAVAILABLE")).toBe("Not yet reported");
    expect(figure(100, "METRIC_UNAVAILABLE")).toBe("Not yet reported");
    expect(figure(99.9, "MEASURED")).toBe("99.9%");
  });
  it("words blockers in plain language and every status has a label", () => {
    expect(blockerText("SCOPE_MISMATCH", "")).toContain("different entity");
    expect(blockerText("SOMETHING_NEW", "extra detail")).toContain("something new");
    for (const s of ["DRAFT", "APPROVAL_PENDING", "PUBLISHED", "EXPIRED", "SUPERSEDED", "EMERGENCY_WITHDRAWN"]) expect(STATUS[s]?.text).toBeTruthy();
    expect(hoursText(null)).toBe("No withdrawals yet");
    expect(hoursText(0.2)).toBe("under an hour");
    expect(shortHash("a".repeat(64))).toHaveLength(11);
  });
});
