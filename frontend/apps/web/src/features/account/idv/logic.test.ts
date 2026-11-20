import { describe, expect, it } from "vitest";

import { applicantText, AUTO_ROUTE, chipLink, detailsProblem, handoff, imageProblem, isAuto, nextStep, onPhone, outcomeOf, stepsFor, trimDetails, WEB_ROUTE } from "./logic";

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

describe("the automatic route", () => {
  const ok = { surname: "Mueller", given_names: "Erika", document_number: "C01X00T47", birth_date: "1985-03-12" };
  it("asks for all four details before anything is sent", () => {
    expect(detailsProblem(ok)).toBeNull();
    expect(detailsProblem({ ...ok, surname: " " })).not.toBeNull();
    expect(detailsProblem({ ...ok, document_number: "ab" })).not.toBeNull();
    expect(detailsProblem({ ...ok, birth_date: "" })).not.toBeNull();
    expect(detailsProblem({ ...ok, birth_date: "2999-01-01" })).not.toBeNull();
  });
  it("trims what is typed", () => {
    expect(trimDetails({ ...ok, surname: "  Mueller " }).surname).toBe("Mueller");
  });
  it("asks again after an undecided result, since nobody reviews it, and otherwise waits at the status", () => {
    const base = { state: "OPEN", route: AUTO_ROUTE, signals: { optical: "REVIEW" }, attempts: {} };
    expect(nextStep({ ...base, decision: "INDETERMINATE" } as never)).toBe("DOCUMENT");
    expect(nextStep({ ...base, decision: "APPROVED", state: "CLOSED" } as never)).toBe("STATUS");
  });
  it("explains a mismatch plainly", () => {
    expect(applicantText("DECLARED_DATA_DIFFERS_FROM_DOCUMENT")).toMatch(/do not match/);
    expect(isAuto(WEB_ROUTE)).toBe(false);
  });
});

describe("the face-match route", () => {
  it("is what both documents take", async () => {
    const { FACE_ROUTE, isFace, routeFor } = await import("./logic");
    expect(routeFor()).toBe(FACE_ROUTE);
    expect(isFace("WEB_FACE")).toBe(true);
    expect(isFace("WEB_AUTO")).toBe(false);
  });

  it("asks for a retake, never a rejection, until a match, and counts as automatic for what happens next", async () => {
    const { nextStep, simpleState } = await import("./logic");
    const signals = { optical: "DONE", nfc_read: "NOT_RUN", passive_auth: "NOT_RUN", chip_auth: "NOT_RUN", liveness: "NOT_RUN", face_match: "NOT_RUN" } as never;
    const open = { route: "WEB_FACE", decision: "INDETERMINATE", signals, state: "OPEN", attempts: {} } as never;
    expect(simpleState(open)).toBe("RETAKE");
    expect(nextStep(open)).toBe("DOCUMENT");
    expect(simpleState({ route: "WEB_FACE", decision: "APPROVED", signals } as never)).toBe("VERIFIED");
    expect(simpleState({ route: "WEB_FACE", decision: "REJECTED", signals } as never)).toBe("REJECTED");
  });

  it("explains each reason in words and not as a finding against the person", async () => {
    const { applicantText } = await import("./logic");
    for (const code of ["FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO", "FACE_NOT_FOUND_OR_UNCLEAR", "FACE_MATCH_NOT_RUN"]) {
      const text = applicantText(code);
      expect(text).not.toMatch(/person to look at this case/); // the general fallback means the code was not recognised
      expect(text.length).toBeGreaterThan(20);
    }
  });
});

describe("wording for what the face route can refuse", () => {
  it("has words for each refusal, never the general fallback", async () => {
    const { applicantText } = await import("./logic");
    for (const code of ["DETAILS_DO_NOT_MATCH_DOCUMENT", "FACE_CONSENT_REQUIRED", "LIVE_PHOTO_REQUIRED", "FACE_ENGINE_UNAVAILABLE", "FACE_ROUTE_NOT_ENABLED", "SCAN_UNAVAILABLE"]) {
      expect(applicantText(code)).not.toMatch(/person to look at this case/);
    }
  });
});
