import { describe, expect, it } from "vitest";

import { blockedText, providerTone } from "./provider-logic";

const closed = { open: false, recent_failures: 0, opened_at: null };

describe("screening providers", () => {
  it("says each blocking control in words and an unknown code as itself", () => {
    for (const c of ["STATE_DISABLED", "STATE_SANDBOX", "FINANCE_AUTHORISATION_MISSING", "NO_MONTHLY_BUDGET", "ADAPTER_NOT_REGISTERED", "NO_APPROVED_REGIONS"]) {
      expect(blockedText(c)).not.toBe(c);
    }
    expect(blockedText("NEW_CODE")).toBe("NEW_CODE");
  });

  it("shows a provider as ready only when approved with nothing blocking it and its circuit closed", () => {
    expect(providerTone({ state: "APPROVED", blocked_by: [], circuit: closed })).toBe("ok");
    expect(providerTone({ state: "APPROVED", blocked_by: ["NO_MONTHLY_BUDGET"], circuit: closed })).toBe("warn");
    expect(providerTone({ state: "DISABLED", blocked_by: ["STATE_DISABLED"], circuit: closed })).toBe("neutral");
    expect(providerTone({ state: "APPROVED", blocked_by: [], circuit: { ...closed, open: true } })).toBe("danger");
  });
});
