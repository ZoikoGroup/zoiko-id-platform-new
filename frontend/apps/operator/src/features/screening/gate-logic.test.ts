import { describe, expect, it } from "vitest";

import { blockerText, GATES, headline, MODULES, reasonProblem, signProblem, validMarket } from "./gate-logic";

describe("release gates", () => {
  it("lists the four gates and the four modules", () => {
    expect(GATES).toEqual(["G0", "G1", "G2", "G3"]);
    expect(MODULES.map((m) => m.id)).toEqual(["SANCTIONS", "PEP_RCA", "ADVERSE_MEDIA", "MONITORING"]);
  });
  it("takes a two letter market only", () => {
    expect(validMarket("gb")).toBe(true);
    expect(validMarket(" GB ")).toBe(true);
    expect(validMarket("GBR")).toBe(false);
    expect(validMarket("")).toBe(false);
  });
  it("refuses a signature with no evidence or an impossible validity", () => {
    expect(signProblem("", 30)).toMatch(/evidence/);
    expect(signProblem("ev-1", 0)).toMatch(/1 to 365/);
    expect(signProblem("ev-1", 366)).toMatch(/1 to 365/);
    expect(signProblem("ev-1", 1.5)).toMatch(/1 to 365/);
    expect(signProblem("ev-1", 180)).toBeNull();
  });
  it("needs a reason of some substance for a defect or a hold", () => {
    expect(reasonProblem(" a ")).toMatch(/three/);
    expect(reasonProblem("provider incident")).toBeNull();
  });
  it("turns blockers into sentences and says plainly whether it is go", () => {
    expect(blockerText("OPEN_P0_DEFECTS:1")).toMatch(/critical defect/);
    expect(blockerText("HELD:provider incident")).toMatch(/provider incident/);
    expect(blockerText("G3 needs DPO")).toBe("G3 needs DPO");
    expect(headline({ go: true, blockers: [] }).tone).toBe("ok");
    expect(headline({ go: false, blockers: ["a"] }).text).toMatch(/1 thing still/);
    expect(headline({ go: false, blockers: ["a", "b"] }).text).toMatch(/2 things still/);
  });
});
