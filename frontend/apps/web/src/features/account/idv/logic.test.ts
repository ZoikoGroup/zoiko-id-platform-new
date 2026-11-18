import { describe, expect, it } from "vitest";

import { applicantText, chipLink, handoff, imageProblem, nextStep, onPhone, outcomeOf, stepsFor, WEB_ROUTE } from "./logic";

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
  it("the browser-only route has no chip and no selfie step: the photograph, then reviewers decide", () => {
    const web = { ...base, route: WEB_ROUTE };
    expect(nextStep(web)).toBe("DOCUMENT");
    expect(nextStep({ ...web, signals: { ...sig, optical: "PASS" } })).toBe("STATUS");
    expect(stepsFor(WEB_ROUTE)).toEqual(["START", "NOTICE", "DOCUMENT", "STATUS"]);
    expect(stepsFor("CHIP")).toContain("CHIP");
  });
  it("tells the applicant plainly that people review a browser-only check, and never that a chip was read", () => {
    expect(applicantText("ALTERNATE_ROUTE_NEEDS_INDEPENDENT_REVIEWERS")).toMatch(/independently/);
    expect(applicantText("ROUTE_NOT_ALLOWED_BY_POLICY")).toMatch(/not available for that country/);
    expect(applicantText("ALTERNATE_ROUTE_NEEDS_INDEPENDENT_REVIEWERS")).not.toMatch(/chip was read/i);
  });
});

describe("the hand-off to the phone app", () => {
  it("builds a link that carries only the case and market, never a secret", () => {
    const link = chipLink({ case_id: "c-1/2", market: "GB" });
    expect(link).toBe("zoikoid://identity-check?case=c-1%2F2&market=GB");
    expect(chipLink({ case_id: "c1", market: "GB" }, "https://app.example/open")).toBe("https://app.example/open?case=c1&market=GB");
  });
  it("tells a phone from a computer by the user agent", () => {
    expect(onPhone("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit")).toBe(true);
    expect(onPhone("Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/120 Mobile")).toBe(true);
    expect(onPhone("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120")).toBe(false);
  });
  it("waits for the app only after the photograph on the chip route, and stops once the chip is read or the case closes", () => {
    expect(handoff(base)).toBe("NOT_NEEDED");
    const photo = { ...base, signals: { ...sig, optical: "PASS" } };
    expect(handoff(photo)).toBe("WAITING_FOR_APP");
    expect(handoff({ ...photo, signals: { ...photo.signals, nfc_read: "READ_OK" } })).toBe("CHIP_DONE");
    expect(handoff({ ...photo, state: "CLOSED" as const })).toBe("NOT_NEEDED");
    expect(handoff({ ...photo, route: WEB_ROUTE })).toBe("NOT_NEEDED");
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

describe("what the applicant is told when a reviewer acts", () => {
  it("says a new photograph was asked for, costs no attempt, and a rejection is not an accusation", () => {
    expect(applicantText("RECAPTURE_REQUESTED:GLARE_OR_SHADOW")).toMatch(/clearer photograph.*not count/);
    expect(applicantText("REVIEWER_REJECTED:DOCUMENT_EXPIRED")).toMatch(/could not accept this document/);
    expect(applicantText("REVIEWER_REJECTED:DOCUMENT_MANIPULATION_SUSPECTED")).not.toMatch(/fraud|forg|alter|fake/i);
  });
  it("tells a person to retake the photograph when the date could not be read, and not to rely on a browser route that is not offered", () => {
    expect(applicantText("DOCUMENT_DATE_NOT_ESTABLISHED")).toMatch(/Take the photograph again/);
    expect(applicantText("MEDIA_RETENTION_NOT_SET")).toMatch(/not available for your country/);
  });
});
