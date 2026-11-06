/** What a person is to the company: a plain member is recognised, named in words, and never taken for an administrator. */
import type { TenantView } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { standing } from "./shared";

const tenant = (roles: { role: string; status: string }[], extra: Partial<TenantView> = {}) =>
  ({ my_roles: roles, signup_journey: "ORGANIZATION", organization_id: "org-1", ...extra }) as unknown as TenantView;

describe("standing: a plain member", () => {
  it("is a member with no administration, named Member", () => {
    const s = standing(tenant([{ role: "MEMBER", status: "ACTIVE" }]));
    expect(s).toMatchObject({ isMember: true, isAdmin: false, isOwner: false, roleLabel: "Member", invited: false });
  });

  it("is not a member while only invited", () => {
    const s = standing(tenant([{ role: "MEMBER", status: "INVITED" }]));
    expect(s).toMatchObject({ isMember: false, invited: true, roleLabel: null });
  });

  it("is not a member once an administrator role is held, and is named by the highest role", () => {
    expect(standing(tenant([{ role: "MEMBER", status: "ACTIVE" }, { role: "ADMIN", status: "ACTIVE" }]))).toMatchObject({ isMember: false, isAdmin: true, roleLabel: "Administrator" });
    expect(standing(tenant([{ role: "OWNER", status: "ACTIVE" }]))).toMatchObject({ isAdmin: true, roleLabel: "Owner" });
  });

  it("has no role label without a company record or without a role", () => {
    expect(standing(null)).toMatchObject({ isMember: false, roleLabel: null });
    expect(standing(tenant([]))).toMatchObject({ isMember: false, roleLabel: null });
  });
});
