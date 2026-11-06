import { describe, expect, it } from "vitest";

import { nextMoves } from "./views";

describe("nextMoves", () => {
  it("follows the order the work goes and stops when closed", () => {
    expect(nextMoves("REQUESTED")).toEqual(["IN_PROGRESS", "DECLINED"]);
    expect(nextMoves("IN_PROGRESS")).toEqual(["ACTIVE", "DECLINED"]);
    for (const s of ["ACTIVE", "DECLINED", "WITHDRAWN"] as const) expect(nextMoves(s)).toEqual([]);
  });
});
