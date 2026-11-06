import { describe, expect, it } from "vitest";

import { cleanPrefix, room } from "./sandbox-logic";

describe("sandbox logic", () => {
  it("makes a prefix the API accepts", () => {
    expect(cleanPrefix("Payroll Team!")).toBe("payroll-team");
    expect(cleanPrefix("123")).toBe("test");
    expect(cleanPrefix("  ")).toBe("test");
    expect(cleanPrefix("a".repeat(50))).toHaveLength(31);
  });
  it("says how many more can be made", () => {
    expect(room(0, 50)).toBe(20);
    expect(room(45, 50)).toBe(5);
    expect(room(50, 50)).toBe(0);
  });
});
