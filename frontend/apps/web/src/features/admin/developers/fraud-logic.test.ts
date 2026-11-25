import { describe, expect, it } from "vitest";

import { actionButtons, coverageText, leadActions, riskTone } from "./fraud-logic";

describe("fraud console rules", () => {
  it("lets only an escalated, unreviewed lead take a finding", () => {
    expect(leadActions({ status: "OPEN", finding: "UNREVIEWED" })).toEqual({ analyze: true, escalate: true, clear: true, finding: false });
    expect(leadActions({ status: "ESCALATED", finding: "UNREVIEWED" }).finding).toBe(true);
    expect(leadActions({ status: "ESCALATED", finding: "CONFIRMED" }).finding).toBe(false);
    expect(leadActions({ status: "CLEARED", finding: "UNREVIEWED" })).toEqual({ analyze: false, escalate: false, clear: false, finding: false });
  });
  it("never offers the maker approval of their own proposal and lets only an approved one be revoked", () => {
    expect(actionButtons({ status: "PROPOSED", maker: "u1" }, "u1")).toEqual({ approve: false, reject: true, revoke: false });
    expect(actionButtons({ status: "PROPOSED", maker: "u1" }, "u2").approve).toBe(true);
    expect(actionButtons({ status: "APPROVED", maker: "u1" }, "u2")).toEqual({ approve: false, reject: false, revoke: true });
    expect(actionButtons({ status: "PROPOSED", maker: "u1" }, null).approve).toBe(true);
  });
  it("never reads a lack of data as safety", () => {
    expect(coverageText({ sessions: 0, sessions_with_observation: 0, sessions_without: 0 })).toBe("No sessions yet.");
    expect(coverageText({ sessions: 10, sessions_with_observation: 0, sessions_without: 10 })).toMatch(/no data, not no risk/);
    expect(coverageText({ sessions: 10, sessions_with_observation: 7, sessions_without: 3 })).toMatch(/not shown as safe/);
    expect(riskTone("HIGH")).toBe("danger");
    expect(riskTone("LOW")).toBe("neutral");
  });
});
