/** Bulk export form helpers: the receipt ids typed in, and how long a package can still be downloaded. */
import { describe, expect, it } from "vitest";

import { MAX_RECEIPTS, parseReceiptIds, timeLeft } from "./bulk-exports-logic";

const A = "0b1c2d3e-4f50-4162-8374-95a6b7c8d9e0";
const B = "11111111-2222-4333-8444-555555555555";

describe("parseReceiptIds", () => {
  it("takes ids one per line or separated by commas and spaces, lower-cases and de-duplicates them", () => {
    const r = parseReceiptIds(`${A}\n${B.toUpperCase()}, ${A} ; ${B}`);
    expect(r).toEqual({ ids: [A, B], invalid: [], tooMany: false });
  });

  it("names what is not a receipt id and ignores blanks", () => {
    const r = parseReceiptIds(`${A}\n\nnot-an-id\n12345`);
    expect(r.ids).toEqual([A]);
    expect(r.invalid).toEqual(["not-an-id", "12345"]);
  });

  it("flags more receipts than one export may hold", () => {
    const many = Array.from({ length: MAX_RECEIPTS + 1 }, (_, i) => `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`).join("\n");
    expect(parseReceiptIds(many).tooMany).toBe(true);
    expect(parseReceiptIds(many.split("\n").slice(0, MAX_RECEIPTS).join("\n")).tooMany).toBe(false);
  });

  it("accepts nothing as nothing", () => {
    expect(parseReceiptIds("  \n ")).toEqual({ ids: [], invalid: [], tooMany: false });
  });
});

describe("timeLeft", () => {
  const now = Date.parse("2026-11-05T12:00:00Z");
  it("says what is left of the download window", () => {
    expect(timeLeft("2026-11-05T12:30:00Z", now)).toBe("30 min left");
    expect(timeLeft("2026-11-05T15:20:00Z", now)).toBe("3 h 20 min left");
    expect(timeLeft("2026-11-09T12:00:00Z", now)).toBe("4 days left");
  });
  it("says Expired once the window has passed or the date is not one", () => {
    expect(timeLeft("2026-11-05T11:59:00Z", now)).toBe("Expired");
    expect(timeLeft("garbage", now)).toBe("Expired");
  });
});
