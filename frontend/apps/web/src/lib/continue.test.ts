import { describe, expect, it } from "vitest";

import { safeNext, verifyThenGo } from "./continue";

describe("safeNext", () => {
  it("follows a path inside the console", () => {
    expect(safeNext("/admin/organization/claim/abc/status")).toBe("/admin/organization/claim/abc/status");
    expect(safeNext("/overview")).toBe("/overview");
  });
  it("drops anything that could leave it", () => {
    for (const bad of ["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "overview", "", null, undefined, "/ok\nbad"]) {
      expect(safeNext(bad as string | null | undefined)).toBeNull();
    }
  });
  it("builds the identity-verification address that comes back", () => {
    expect(verifyThenGo("/admin/organization/claim/x/status")).toBe("/account/verification?next=%2Fadmin%2Forganization%2Fclaim%2Fx%2Fstatus");
    expect(safeNext(decodeURIComponent(verifyThenGo("/a").split("next=")[1] ?? ""))).toBe("/a");
  });
});
