import { describe, expect, it } from "vitest";

import { CATALOGS, dirOf, messages, offered } from "./messages";
import { qrMatrix } from "./qr";

describe("the hosted verification's words and layout (WF-01 §06, §10, S06)", () => {
  it("offers only languages that are both reviewed on the server and have a catalog", () => {
    expect(offered(["en", "fr"])).toEqual(["en"]); // no reviewed French catalog: never machine-translated
    expect(offered([])).toEqual(["en"]);
    expect(messages("en-GB")).toBe(CATALOGS.en);
  });

  it("lays out right-to-left languages right to left", () => {
    expect(dirOf("ar")).toBe("rtl");
    expect(dirOf("he-IL")).toBe("rtl");
    expect(dirOf("en")).toBe("ltr");
  });

  it("names every §10 code and never calls finishing 'verified' or accuses anyone", () => {
    for (const [code, t] of Object.entries(CATALOGS)) {
      expect(Object.keys(t.action).sort(), code).toEqual(
        ["CAPTURE_QUALITY", "DEVICE_RISK", "INCONCLUSIVE", "LIMIT_REACHED", "NFC_TRUST_INDETERMINATE", "NFC_UNAVAILABLE", "PROVIDER_TEMPORARILY_UNAVAILABLE",
          "REVIEW_REQUIRED", "SESSION_EXPIRED", "SUSPICIOUS_SIGNAL", "UNSUPPORTED_DOCUMENT"].sort(),
      ); // prettier-ignore
      for (const o of Object.values(t.outcome)) expect(`${o.title} ${o.body}`.toLowerCase()).not.toMatch(/\bverified\b|fraud/);
      for (const a of Object.values(t.action)) expect(a.toLowerCase()).not.toMatch(/fraud|sanction|score/);
    }
  });

  it("draws a real QR code: square, with the three finder patterns", () => {
    const m = qrMatrix(`https://id.example/verify-session#t=${"0".repeat(32)}.${"a".repeat(43)}`);
    const n = m.length;
    expect(n).toBeGreaterThanOrEqual(21);
    expect((n - 17) % 4).toBe(0); // a QR version's size is 17 + 4v
    const at = (r: number, c: number) => m[r]?.[c] === true;
    const finder = (r: number, c: number) => [0, 6].every((i) => at(r + i, c) && at(r, c + i) && at(r + 6, c + i) && at(r + i, c + 6)) && at(r + 3, c + 3);
    expect(finder(0, 0) && finder(0, n - 7) && finder(n - 7, 0)).toBe(true);
  });
});
