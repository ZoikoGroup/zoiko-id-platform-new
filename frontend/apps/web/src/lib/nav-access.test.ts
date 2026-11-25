/** UX-02: an invited person lands as a Member and is not shown administration without an administrator role. */
import type { NavGroup } from "@zoiko/ui";
import { describe, expect, it } from "vitest";

import { can, visibleNav } from "./nav-access";

const icon = (() => null) as unknown as NavGroup["items"][number]["icon"];
const item = (id: string) => ({ id, label: id, href: `/${id}`, icon });
const NAV: NavGroup[] = [
  { label: "Company", items: ["overview", "alerts", "onboarding", "organization", "setup", "domains", "applications", "integrations", "team", "approvals", "billing", "contacts", "checks", "settings"].map(item) },
  { label: "Verify API", items: ["command-center", "idv-cases", "verification-spend"].map(item) },
  { label: "My account", items: ["account", "sessions", "passkeys"].map(item) },
  { label: "Administration", items: ["idp", "verification", "credentials", "authority", "mandates", "policies", "evidence"].map(item) },
];
const ids = (g: NavGroup[]) => g.flatMap((x) => x.items.map((i) => i.id));

describe("UX-02 role default: a Member sees no administration", () => {
  it("shows a Member only Overview, Alerts and their own account", () => {
    const seen = visibleNav(NAV, { isAdmin: false, pendingOwner: false });
    expect(ids(seen)).toEqual(["overview", "alerts", "account", "sessions", "passkeys"]);
    expect(seen.map((g) => g.label)).not.toContain("Administration");
  });

  it("shows a registrant who is not yet confirmed as owner only the pages that confirm the company", () => {
    const seen = ids(visibleNav(NAV, { isAdmin: false, pendingOwner: true }));
    expect(seen).toContain("onboarding");
    expect(seen).toContain("organization");
    expect(seen).toContain("setup"); // the setup checklist is how the registrant gets confirmed
    expect(seen).not.toContain("team");
    expect(seen).not.toContain("approvals");
    expect(ids(visibleNav(NAV, { isAdmin: false, pendingOwner: false }))).not.toContain("setup"); // a Member never sees setup
    expect(seen).not.toContain("authority");
  });

  it("shows Billing, and only Billing, to a Member the Owner gave billing access", () => {
    const seen = ids(visibleNav(NAV, { isAdmin: false, pendingOwner: false, billingAccess: true }));
    expect(seen).toContain("billing");
    expect(seen).not.toContain("team");
    expect(seen).not.toContain("settings");
  });

  it("shows the Command Center, and not the case or spend pages, to a Member the Owner gave an analytics bundle (WF-02)", () => {
    expect(ids(visibleNav(NAV, { isAdmin: false, pendingOwner: false }))).not.toContain("command-center");
    const seen = ids(visibleNav(NAV, { isAdmin: false, pendingOwner: false, analyticsAccess: true }));
    expect(seen).toContain("command-center");
    expect(seen).not.toContain("idv-cases");
    expect(seen).not.toContain("verification-spend");
  });

  // The role matrix cells (ZID-ENG-DEC-007), as `GET /v1/tenant/me/permissions` returns them for one role.
  const APPROVER = { "d.approve-eligible-pending-request": ["A"], "a.approve-privileged-role-grant": ["A"], "d.approve-authority-delegation": ["A"], "f.view-authorized-audit-and-ledger-entries": ["R"], "a.list-active-members-and-assigned-roles": ["R"] };
  const OPERATOR = { "b.draft-routine-credential-issuance": ["X"], "b.upload-organization-kyb-evidence": ["X"], "f.view-authorized-audit-and-ledger-entries": ["R"], "a.list-active-members-and-assigned-roles": ["R"] };
  const VIEWER = { "f.view-authorized-audit-and-ledger-entries": ["R"], "e.view-integration-directory-status": ["R"] };

  it("gives an Approver the approval inbox and no administration they cannot use", () => {
    const seen = ids(visibleNav(NAV, { isAdmin: false, pendingOwner: false, rights: APPROVER }));
    expect(seen).toContain("approvals");
    expect(seen).toContain("authority");
    expect(seen).not.toContain("settings");
    expect(seen).not.toContain("billing");
    expect(seen).not.toContain("idp");
  });

  it("gives an Operator the verification and credential work and nothing that changes policy", () => {
    const seen = ids(visibleNav(NAV, { isAdmin: false, pendingOwner: false, rights: OPERATOR }));
    expect(seen).toContain("credentials");
    expect(seen).toContain("verification");
    expect(seen).not.toContain("approvals");
    expect(seen).not.toContain("policies");
  });

  it("gives a Viewer read-only evidence and nothing else", () => {
    const seen = ids(visibleNav(NAV, { isAdmin: false, pendingOwner: false, rights: VIEWER }));
    expect(seen).toContain("evidence");
    expect(seen).not.toContain("approvals");
    expect(seen).not.toContain("team");
    expect(seen).not.toContain("credentials");
  });

  it("keeps a plain Member on the short menu even when the rights have loaded", () => {
    expect(ids(visibleNav(NAV, { isAdmin: false, pendingOwner: false, rights: {} }))).toEqual(["overview", "alerts", "account", "sessions", "passkeys"]);
  });

  it("answers can() from the modes a role holds", () => {
    expect(can(APPROVER, "d.approve-eligible-pending-request", "A")).toBe(true);
    expect(can(APPROVER, "d.approve-eligible-pending-request", "X")).toBe(false);
    expect(can(undefined, "d.approve-eligible-pending-request")).toBe(false);
  });

  it("shows owners and administrators everything", () => {
    expect(visibleNav(NAV, { isAdmin: true, pendingOwner: false })).toEqual(NAV);
  });

  it("does not change the input", () => {
    const before = JSON.stringify(NAV.map((g) => g.items.map((i) => i.id)));
    visibleNav(NAV, { isAdmin: false, pendingOwner: false });
    expect(JSON.stringify(NAV.map((g) => g.items.map((i) => i.id)))).toBe(before);
  });
});
