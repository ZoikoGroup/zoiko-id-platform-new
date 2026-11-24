import { describe, expect, it } from "vitest";

import { COUNTRIES } from "./countries";
import { CATEGORIES, DOCUMENTS_BY_CATEGORY, IDENTITY_DOCUMENTS, categoryOf, documentLabel, photographAdvice } from "./documents";
import { NO_WIZARD_DETAILS, TOTAL_SCREENS, WIZARD_STEPS, detailsProblem, documentsProblem, firstIncomplete, fullName, marketFor } from "./wizard-logic";

const ok = { given: "Asha", middle: "", family: "Rao", birth: "1990-05-17", country: "IN" };
const photo = { type: "image/jpeg", size: 200_000 };

describe("documents", () => {
  it("offers the same three groups to everyone, with no country in sight", () => {
    expect(CATEGORIES).toEqual(["identity", "address", "other"]);
    expect(IDENTITY_DOCUMENTS.map((d) => d.value)).toContain("passport");
    expect(IDENTITY_DOCUMENTS.map((d) => d.value)).toContain("aadhaar");
    expect(IDENTITY_DOCUMENTS.length).toBe(10);
  });
  it("never treats a bill or statement as an identity document", () => {
    const identity = IDENTITY_DOCUMENTS.map((d) => d.value);
    for (const d of DOCUMENTS_BY_CATEGORY.address) expect(identity).not.toContain(d.value);
    expect(categoryOf("electricity_bill")).toBe("address");
    expect(categoryOf("birth_certificate")).toBe("other");
    expect(categoryOf("passport")).toBe("identity");
    expect(categoryOf("nonsense")).toBeNull();
  });
  it("has no duplicate value anywhere, and a label for every one", () => {
    const all = CATEGORIES.flatMap((c) => DOCUMENTS_BY_CATEGORY[c]);
    expect(new Set(all.map((d) => d.value)).size).toBe(all.length);
    expect(documentLabel("driving_license")).toBe("Driving licence");
    expect(documentLabel("unknown")).toBe("unknown");
  });
  it("tells the person what to photograph", () => {
    expect(photographAdvice("passport")).toMatch(/two lines/);
    expect(photographAdvice("driving_license")).toMatch(/side with your photo/);
  });
  it("lists countries with two-letter codes and no repeats", () => {
    expect(COUNTRIES.every((c) => /^[A-Z]{2}$/.test(c.id))).toBe(true);
    expect(new Set(COUNTRIES.map((c) => c.id)).size).toBe(COUNTRIES.length);
    expect(COUNTRIES.map((c) => c.id)).toEqual(expect.arrayContaining(["GB", "IN", "US", "BR", "ZA"]));
  });
});

describe("the wizard", () => {
  it("has six screens counting the result", () => {
    expect(WIZARD_STEPS).toEqual(["INTRO", "DETAILS", "HOW", "DOCUMENTS", "REVIEW"]);
    expect(TOTAL_SCREENS).toBe(6);
  });
  it("needs both names and a possible date of birth", () => {
    expect(detailsProblem(NO_WIZARD_DETAILS)).toMatch(/given name/);
    expect(detailsProblem({ ...ok, family: " " })).toMatch(/family name/);
    expect(detailsProblem({ ...ok, birth: "" })).toMatch(/date of birth/);
    expect(detailsProblem({ ...ok, birth: "2999-01-01" })).toMatch(/not possible/);
    expect(detailsProblem({ ...ok, birth: "1850-01-01" })).toMatch(/not possible/);
    expect(detailsProblem(ok)).toBeNull();
  });
  it("does not require a country, and the country never blocks anything", () => {
    expect(detailsProblem({ ...ok, country: "" })).toBeNull();
  });
  it("opens the case for the chosen country, or for the global policy when none is chosen", () => {
    expect(marketFor("in")).toBe("IN");
    expect(marketFor(" gb ")).toBe("GB");
    expect(marketFor("")).toBe("GLOBAL");
    expect(marketFor("India")).toBe("GLOBAL");
  });
  it("joins the name without gaps", () => {
    expect(fullName({ given: "Asha", middle: "", family: "Rao" })).toBe("Asha Rao");
    expect(fullName({ given: " Asha ", middle: "Devi", family: "Rao" })).toBe("Asha Devi Rao");
  });
  it("needs a document type, both photographs and the agreement", () => {
    const good = { docType: "passport", document: photo, selfie: photo, agreed: true };
    expect(documentsProblem({ ...good, docType: "" })).toMatch(/Choose the identity document/);
    expect(documentsProblem({ ...good, document: null })).toMatch(/photograph of the document/);
    expect(documentsProblem({ ...good, selfie: null })).toMatch(/live photograph/);
    expect(documentsProblem({ ...good, agreed: false })).toMatch(/Agree/);
    expect(documentsProblem({ ...good, document: { type: "application/pdf", size: 1000 } })).toBeTruthy(); // the platform reads JPEG and PNG
    expect(documentsProblem(good)).toBeNull();
  });
  it("sends a person who skipped ahead back to the first thing missing", () => {
    const good = { details: ok, docType: "passport", document: photo, selfie: photo, agreed: true };
    expect(firstIncomplete(good)).toBeNull();
    expect(firstIncomplete({ ...good, details: NO_WIZARD_DETAILS })).toBe("DETAILS");
    expect(firstIncomplete({ ...good, selfie: null })).toBe("DOCUMENTS");
  });
});
