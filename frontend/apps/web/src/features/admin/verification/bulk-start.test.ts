import { describe, expect, it } from "vitest";

import { parsePeople } from "./bulk-start";

const A = "0b1c2d3e-4f50-4162-8374-95a6b7c8d9e0";

describe("parsePeople", () => {
  it("de-duplicates and reports what is not an id", () => {
    const r = parsePeople(`${A}\n${A.toUpperCase()}, nope`);
    expect(r.ids).toEqual([A]);
    expect(r.invalid).toEqual(["nope"]);
  });
  it("flags more than fifty", () => {
    const many = Array.from({ length: 51 }, (_, i) => `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`).join("\n");
    expect(parsePeople(many).tooMany).toBe(true);
  });
});
