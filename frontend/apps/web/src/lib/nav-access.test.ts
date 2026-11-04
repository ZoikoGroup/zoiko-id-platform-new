/** UX-02: an invited person lands as a Member and is not shown administration without an administrator role. */
import type { NavGroup } from "@zoiko/ui";
import { describe, expect, it } from "vitest";

import { visibleNav } from "./nav-access";

const icon = (() => null) as unknown as NavGroup["items"][number]["icon"];
const item = (id: string) => ({ id, label: id, href: `/${id}`, icon });
const NAV: NavGroup[] = [
  { label: "Company", items: ["overview", "alerts", "onboarding", "organization", "setup", "domains", "applications", "integrations", "team", "approvals", "billing", "contacts", "settings"].map(item) },
  { label: "My account", items: ["account", "sessions", "passkeys"].map(item) },
  { label: "Administration", items: ["idp", "authority", "mandates", "policies"].map(item) },
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

  it("shows owners and administrators everything", () => {
    expect(visibleNav(NAV, { isAdmin: true, pendingOwner: false })).toEqual(NAV);
  });

  it("does not change the input", () => {
    const before = JSON.stringify(NAV.map((g) => g.items.map((i) => i.id)));
    visibleNav(NAV, { isAdmin: false, pendingOwner: false });
    expect(JSON.stringify(NAV.map((g) => g.items.map((i) => i.id)))).toBe(before);
  });
});
