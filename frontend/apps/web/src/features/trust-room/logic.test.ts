import { describe, expect, it } from "vitest";

import { accessText, canDownload, requestReady } from "./logic";

const NOW = new Date("2026-11-25T12:00:00Z");
const base = { status: "APPROVED", revoked: false, grant_expires_at: "2026-11-26T12:00:00Z", downloads_left: 2 };

describe("evidence room rules", () => {
  it("offers a download only while approved, in date, not withdrawn and with downloads left", () => {
    expect(canDownload(base, NOW)).toBe(true);
    expect(canDownload({ ...base, status: "REQUESTED" }, NOW)).toBe(false);
    expect(canDownload({ ...base, revoked: true }, NOW)).toBe(false);
    expect(canDownload({ ...base, grant_expires_at: "2026-11-25T11:00:00Z" }, NOW)).toBe(false);
    expect(canDownload({ ...base, downloads_left: 0 }, NOW)).toBe(false);
  });
  it("words each state plainly", () => {
    expect(accessText({ ...base, status: "DENIED" }, NOW)).toMatch(/declined/);
    expect(accessText({ ...base, status: "REQUESTED", grant_expires_at: null, downloads_left: null }, NOW)).toMatch(/reviewed/);
    expect(accessText({ ...base, downloads_left: 0 }, NOW)).toMatch(/used/);
    expect(accessText(base, NOW)).toMatch(/Ready. 2 download/);
  });
  it("needs a document, a company, a real purpose and the terms before it can be sent", () => {
    const ok = { evidence_id: "e", organization: "Buyer Corp", purpose: "Vendor security review", accept_terms: true };
    expect(requestReady(ok)).toBe(true);
    expect(requestReady({ ...ok, accept_terms: false })).toBe(false);
    expect(requestReady({ ...ok, purpose: "short" })).toBe(false);
    expect(requestReady({ ...ok, evidence_id: "" })).toBe(false);
  });
});
