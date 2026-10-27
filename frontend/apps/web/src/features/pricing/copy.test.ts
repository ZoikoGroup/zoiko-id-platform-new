import { describe, expect, it } from "vitest";

import * as copy from "./copy";

/** ZID-COM-001 §16, §19: the pricing page makes no certification, "compliant with" or availability claim, and the
 * hero and secondary lines are the only headline claims. */
const FORBIDDEN = [/certif/i, /complian/i, /\bSOC ?2\b/i, /ISO ?27001/i, /\bGDPR\b/i, /eIDAS/i, /available in/i, /guarantee/i, /\b99\.\d+%/];

function strings(): string[] {
  const out: string[] = [];
  for (const v of Object.values(copy)) {
    if (typeof v === "string") {
      out.push(v);
    } else if (Array.isArray(v)) {
      for (const row of v) {
        if (typeof row === "string") out.push(row);
        else out.push(...Object.values(row as object).map(String));
      }
    } else if (v && typeof v === "object") {
      out.push(...Object.values(v as object).map(String));
    }
  }
  return out;
}

describe("pricing page copy", () => {
  it("uses the two approved headline lines verbatim", () => {
    expect(copy.HERO).toBe("Identity infrastructure that scales with your trust requirements.");
    expect(copy.SUBHERO).toBe("Start building free. Add verification, federation and governance when your use case demands it.");
  });

  it("makes no certification, compliance, availability or uptime claim", () => {
    for (const text of strings()) for (const bad of FORBIDDEN) expect(text, `${bad} in "${text}"`).not.toMatch(bad);
  });

  it("states that the calculator is an estimate and that taxes and supplier checks are excluded", () => {
    expect(copy.DISCLAIMER).toMatch(/estimate, not a quotation/);
    expect(copy.DISCLAIMER).toMatch(/exclude taxes and third-party checks/);
  });

  it("lists the five plans in the proposal order", () => {
    expect([...copy.PLAN_ORDER]).toEqual(["DEVELOPER", "LAUNCH", "BUSINESS", "SCALE", "ENTERPRISE"]);
  });
});
