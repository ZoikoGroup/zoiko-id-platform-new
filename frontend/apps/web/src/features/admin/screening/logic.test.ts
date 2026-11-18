import { describe, expect, it } from "vitest";

import { caseProblem, EMPTY_CASE, ERROR_TEXT, FINDING_TONE, ITEM_LABEL, saysTooMuch, splitList, type CaseForm } from "./logic";

const TENANT = "11111111-1111-4111-8111-111111111111";
const good: CaseForm = { ...EMPTY_CASE, market: "gb", name: "Acme Widgets Limited", subjectRef: TENANT };

describe("screening request form", () => {
  it("accepts a company screened as the caller's own organization", () => {
    expect(caseProblem(good, TENANT)).toBeNull();
  });

  it("names what is wrong with the shape of a request", () => {
    expect(caseProblem({ ...good, market: "GBR" }, TENANT)).toContain("two-letter");
    expect(caseProblem({ ...good, purpose: " " }, TENANT)).toContain("what the screening is for");
    expect(caseProblem({ ...good, name: "A" }, TENANT)).toContain("name");
    expect(caseProblem({ ...good, birth: "01/01/1970" }, TENANT)).toContain("birth year");
    expect(caseProblem({ ...good, birth: "1970-01-01" }, TENANT)).toBeNull();
    expect(caseProblem({ ...good, classes: [] }, TENANT)).toContain("at least one");
  });

  it("never lets a company other than the caller's own be screened", () => {
    expect(caseProblem({ ...good, subjectRef: "22222222-2222-4222-8222-222222222222" }, TENANT)).toContain("your own organization");
  });

  it("needs a real identifier for a person", () => {
    const person: CaseForm = { ...good, subjectType: "UBO", subjectRef: "not-an-id" };
    expect(caseProblem(person, TENANT)).toContain("account identifier");
    expect(caseProblem({ ...person, subjectRef: "33333333-3333-4333-8333-333333333333" }, TENANT)).toBeNull();
  });

  it("splits a list of countries", () => {
    expect(splitList("gb, fr;\nde ,")).toEqual(["gb", "fr", "de"]);
    expect(splitList("")).toEqual([]);
  });

  it("flags wording a result must never use, and tones the findings the platform gives", () => {
    expect(saysTooMuch("AML approved")).toBe(true);
    expect(saysTooMuch("This is clean")).toBe(true);
    expect(saysTooMuch("No candidate was found in the checked sources")).toBe(false);
    expect(FINDING_TONE["Review pending"]).toBe("warn");
    expect(FINDING_TONE["Restricted for this use"]).toBe("danger");
  });
});

describe("information the customer is asked for", () => {
  it("labels every item the reviewer can ask for and never says why", () => {
    expect(Object.keys(ITEM_LABEL).sort()).toEqual(["IDENTITY_CLARIFICATION", "OWNERSHIP_EXPLANATION", "PURPOSE_OF_RELATIONSHIP", "SOURCE_OF_FUNDS", "SOURCE_OF_WEALTH", "SUPPORTING_DOCUMENT"]);
    for (const text of Object.values(ITEM_LABEL)) expect(text).not.toMatch(/sanction|pep|politically|media|screening|risk/i);
  });
  it("has words for the answers a customer can meet", () => {
    expect(ERROR_TEXT.REQUEST_NOT_FOUND).toMatch(/nothing more/);
    expect(ERROR_TEXT.REQUEST_EXPIRED).toMatch(/contact us/);
  });
});
