import { describe, expect, it } from "vitest";

import { csvCell, toCsv } from "../src/utils/csv";
import { lineDiff } from "../src/patterns/governance";

describe("csv export (UX-26)", () => {
  it("neutralizes formula injection", () => {
    for (const v of ["=1+1", "+cmd", "-2", "@SUM(A1)", "\tx"]) expect(csvCell(v).replace(/^"/, "").startsWith("'")).toBe(true);
  });
  it("quotes separators and quotes", () => {
    expect(csvCell('a,"b"')).toBe('"a,""b"""');
  });
  it("prefixes a BOM and uses CRLF", () => {
    expect(toCsv(["a"], [["1"]])).toBe("﻿a\r\n1");
  });
});

describe("lineDiff", () => {
  it("marks added and removed lines", () => {
    const d = lineDiff("a\nb\nc", "a\nx\nc");
    expect(d.map((l) => l.kind)).toEqual(["same", "del", "add", "same"]);
  });
});
