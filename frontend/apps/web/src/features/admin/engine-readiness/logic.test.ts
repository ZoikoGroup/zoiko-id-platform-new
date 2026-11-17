import { describe, expect, it } from "vitest";

import { explain, groupByOwner, licenceState, MODE_WORDS, percent } from "./logic";

const base = { id: "x", role: null, code_licence: null, model_licence_confirmed: false, licence_verified_on: null, status: null, in_engine: true };

describe("explain", () => {
  it("names the owner and the reason for each fixed code", () => {
    expect(explain("DPIA_REFERENCE_MISSING").owner).toBe("Privacy");
    expect(explain("LICENCE_CHECK_NOT_RECORDED").owner).toBe("Legal");
    expect(explain("EVALUATION_TOO_OLD").text).toMatch(/more than a year old/);
    expect(explain("ENGINE_SCOPE_MISSING").owner).toBe("Product and engineering");
  });
  it("reads component codes", () => {
    const e = explain("COMPONENT_SILENT_FACE_ANTI_SPOOFING_COMMERCIAL_USE_NOT_CONFIRMED");
    expect(e.owner).toBe("Legal");
    expect(e.text).toBe("The component “silent-face-anti-spoofing” is not confirmed for commercial use.");
    expect(explain("COMPONENT_DEEPFACE_LICENCE_NOT_VERIFIED").text).toMatch(/deepface.*no date/);
    expect(explain("COMPONENT_X_NOT_IN_LICENCE_REGISTER").text).toMatch(/not in the licence register/);
    expect(explain("COMPONENT_X_MODEL_LICENCE_NOT_CONFIRMED").text).toMatch(/pretrained-model licence/);
  });
  it("reads measured-rate codes", () => {
    expect(explain("MEASURED_FALSE_ACCEPT_ABOVE_CEILING").text).toBe("The measured false accept rate is above the ceiling this platform allows.");
    expect(explain("MEASURED_BONA_FIDE_REJECT_MISSING").text).toBe("The measured genuine-user reject rate is not recorded.");
  });
  it("shows an unknown code as itself", () => {
    expect(explain("SOMETHING_NEW")).toEqual({ code: "SOMETHING_NEW", owner: "Product and engineering", text: "SOMETHING_NEW" });
  });
});

describe("groupByOwner", () => {
  it("groups in a stable order and drops empty groups", () => {
    const g = groupByOwner(["ENGINE_ID_MISSING", "DPIA_REFERENCE_MISSING", "LICENCE_CHECK_NOT_RECORDED", "EVALUATION_ID_MISSING"]);
    expect(g.map((x) => x.owner)).toEqual(["Legal", "Privacy", "Security and the evaluation lab", "Product and engineering"]);
    expect(groupByOwner([])).toEqual([]);
  });
});

describe("licenceState", () => {
  it("never says a licence is fine without a recorded reading", () => {
    expect(licenceState({ ...base, commercial_use: "UNKNOWN" }).label).toBe("Not read yet");
    expect(licenceState({ ...base, commercial_use: "YES" }).label).toBe("Partly recorded");
    expect(licenceState({ ...base, commercial_use: "YES", model_licence_confirmed: true }).label).toBe("Partly recorded");
    expect(licenceState({ ...base, commercial_use: "YES", model_licence_confirmed: true, licence_verified_on: "2026-11-01" })).toEqual({ label: "Verified 2026-11-01", tone: "ok" });
    expect(licenceState({ ...base, commercial_use: "NO", model_licence_confirmed: true, licence_verified_on: "2026-11-01" }).tone).toBe("danger");
  });
});

describe("words", () => {
  it("says what each mode means and formats rates", () => {
    expect(MODE_WORDS.DISABLED.text).toMatch(/without one/);
    expect(percent(null)).toBe("not recorded");
    expect(percent(0.0001)).toBe("0.010%");
    expect(percent(0.05)).toBe("5.0%");
  });
});
