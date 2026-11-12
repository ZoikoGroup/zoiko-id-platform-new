import { describe, expect, it } from "vitest";

import { regulatoryNotice } from "./regulatory-errors";

describe("regulatoryNotice", () => {
  it("explains both policy pack refusals in plain words and says nothing was stored", () => {
    for (const code of ["REGULATORY_POLICY_UNAVAILABLE", "REGULATORY_REVIEW_REQUIRED"]) {
      const n = regulatoryNotice(code);
      expect(n).not.toBeNull();
      expect(n?.title.length).toBeGreaterThan(10);
      expect(n?.body).toMatch(/nothing was started or stored/i);
    }
  });

  it("never shows a raw code or a capability name to the person", () => {
    for (const code of ["REGULATORY_POLICY_UNAVAILABLE", "REGULATORY_REVIEW_REQUIRED"]) {
      const text = JSON.stringify(regulatoryNotice(code));
      expect(text).not.toMatch(/REGULATORY_|INDIVIDUAL_VERIFICATION|policy pack/);
    }
  });

  it("leaves every other code to the uniform error view", () => {
    expect(regulatoryNotice("COUNTRY_CAPABILITY_UNAVAILABLE")).toBeNull();
    expect(regulatoryNotice("")).toBeNull();
    expect(regulatoryNotice(undefined)).toBeNull();
  });
});
