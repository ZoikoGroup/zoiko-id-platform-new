import { describe, expect, it } from "vitest";

import { applicantText, imageProblem, nextStep, outcomeOf } from "./logic";

const sig = { optical: "NOT_STARTED", nfc_read: "NOT_STARTED", passive_auth: "NOT_TESTED", chip_auth: "NOT_TESTED", liveness: "NOT_RUN", face_match: "NOT_RUN" };
const base = { state: "OPEN" as const, route: "CHIP", signals: sig, decision: "INDETERMINATE" as const, attempts: {} };

describe("the journey", () => {
  it("goes document, chip, selfie, then status, and a closed case is only status", () => {
    expect(nextStep(base)).toBe("DOCUMENT");
    expect(nextStep({ ...base, signals: { ...sig, optical: "PASS" } })).toBe("CHIP");
    expect(nextStep({ ...base, signals: { ...sig, optical: "PASS", nfc_read: "READ_OK" } })).toBe("SELFIE");
    expect(nextStep({ ...base, signals: { ...sig, optical: "PASS", nfc_read: "READ_OK", liveness: "PASS" } })).toBe("STATUS");
    expect(nextStep({ ...base, state: "CLOSED" })).toBe("STATUS");
  });
  it("skips the chip on an alternative route", () => {
    expect(nextStep({ ...base, route: "PASSPORT_OPTICAL_REVIEW", signals: { ...sig, optical: "PASS" } })).toBe("SELFIE");
  });
});

describe("the photograph", () => {
  it("refuses what the platform would refuse, before anything is sent", () => {
    expect(imageProblem(null)).toMatch(/Choose/);
    expect(imageProblem({ type: "application/pdf", size: 100_000 })).toMatch(/JPEG or PNG/);
    expect(imageProblem({ type: "image/jpeg", size: 7 * 1024 * 1024 })).toMatch(/too large/);
    expect(imageProblem({ type: "image/png", size: 100 })).toMatch(/too small/);
    expect(imageProblem({ type: "image/jpeg", size: 400_000 })).toBeNull();
  });
});

describe("words for the applicant", () => {
  it("never accuses on missing evidence and never explains how to beat a check", () => {
    expect(applicantText("CHIP_TRUST_ROOT_MISSING")).toMatch(/Nothing is wrong with your passport/);
    expect(applicantText("VIRTUAL_CAMERA_DETECTED")).toMatch(/person to look/); // the general line, not a hint
    expect(applicantText("PRESENTATION_ATTACK_DETECTED")).not.toMatch(/attack|spoof|fake/i);
    expect(applicantText("BIOMETRICS_OFF:NO_IMPACT_ASSESSMENT")).toMatch(/not switched on/);
    expect(outcomeOf("INDETERMINATE").text).toMatch(/not a finding against you/);
  });
});
