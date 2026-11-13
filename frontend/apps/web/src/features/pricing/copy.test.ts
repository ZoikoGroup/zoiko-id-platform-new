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
  it("uses the approved headline and positioning line (ZID-UX-PRICE-004), with no speed or accuracy figure and no trial", () => {
    expect(copy.HERO).toBe("Verify identity. Validate credentials. Prove authority.");
    expect(copy.SUBHERO).toMatch(/^One platform to verify identity, validate qualifications, prove authority, and exchange trusted evidence/);
    for (const text of [copy.HERO, copy.SUBHERO]) expect(text).not.toMatch(/free|trial|\d/i);
  });

  it("makes no certification, compliance, availability or uptime claim", () => {
    for (const text of strings()) for (const bad of FORBIDDEN) expect(text, `${bad} in "${text}"`).not.toMatch(bad);
  });

  it("states that the calculator is an estimate and that taxes and supplier checks are excluded", () => {
    expect(copy.DISCLAIMER).toMatch(/estimate, not a quotation/);
    expect(copy.DISCLAIMER).toMatch(/exclude taxes and third-party checks/);
  });

  it("answers each buyer question once and never promises a price, a trial or an uptime", () => {
    expect(copy.FAQ.length).toBeGreaterThanOrEqual(6);
    expect(new Set(copy.FAQ.map((f) => f.q)).size).toBe(copy.FAQ.length);
    for (const f of copy.FAQ) expect(`${f.q} ${f.a}`).not.toMatch(/[$€£]|\bper (month|check)\b|\bfree\b|uptime|unlimited|best|cheapest/i);
  });

  it("lists the five plans in the proposal order", () => {
    expect([...copy.PLAN_ORDER]).toEqual(["DEVELOPER", "LAUNCH", "BUSINESS", "SCALE", "ENTERPRISE"]);
  });
});

describe("partner page copy", () => {
  it("promises no price, margin, discount or checkout and makes no forbidden claim", () => {
    const text = [copy.PARTNER_HERO, copy.PARTNER_INTRO, ...copy.PARTNER_TYPES_COPY.flatMap((t) => [t.label, t.text]), ...copy.PARTNER_TERMS.flatMap((t) => [t.label, t.text])];
    for (const t of text) {
      expect(t).not.toMatch(/[$€£]|\d|\bfree\b|discount|commission|margin %|guarantee|certif|complian/i);
      for (const bad of FORBIDDEN) expect(t).not.toMatch(bad);
    }
    expect(copy.PARTNER_INTRO).toMatch(/no partner checkout/);
  });
});
