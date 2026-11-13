import { describe, expect, it } from "vitest";

import { regulatoryNotice } from "./regulatory-errors";

const CODES = [
  "REGULATORY_POLICY_UNAVAILABLE",
  "REGULATORY_REVIEW_REQUIRED",
  "MARKET_NOT_RELEASED",
  "PROVIDER_NOT_ENTITLED",
  "PROVIDER_SCOPE_SUSPENDED",
  "CROSS_BORDER_ROUTE_DENIED",
  "BIOMETRIC_ROUTE_NOT_PERMITTED",
  "PRIVACY_OVERLAY_UNAVAILABLE",
];

describe("regulatoryNotice", () => {
  it("explains every regulatory refusal in plain words and says nothing was stored", () => {
    for (const code of CODES) {
      const n = regulatoryNotice(code);
      expect(n).not.toBeNull();
      expect(n?.title.length).toBeGreaterThan(10);
      expect(n?.body).toMatch(/nothing was started or stored/i);
    }
  });

  it("never shows a raw code or a capability name to the person", () => {
    for (const code of CODES) {
      const text = JSON.stringify(regulatoryNotice(code));
      expect(text).not.toMatch(/REGULATORY_|MARKET_|PROVIDER_|CROSS_BORDER|BIOMETRIC_|PRIVACY_|INDIVIDUAL_VERIFICATION|policy pack|release|entitle|overlay/);
    }
  });

  it("leaves every other code to the uniform error view", () => {
    expect(regulatoryNotice("COUNTRY_CAPABILITY_UNAVAILABLE")).toBeNull();
    expect(regulatoryNotice("")).toBeNull();
    expect(regulatoryNotice(undefined)).toBeNull();
  });
});
