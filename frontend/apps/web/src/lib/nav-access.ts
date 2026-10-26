import type { NavGroup } from "@zoiko/ui";

/** What the signed-in person is to the company (see `standing` in the onboarding feature). */
export type NavStanding = { isAdmin: boolean; pendingOwner: boolean };

/** Company administration. A person without an administrator role is not shown these controls (UX-02): the cell still
 * refuses the calls by itself (UX-03), so hiding is courtesy, never the protection. */
const ADMIN_ITEMS = new Set([
  "onboarding", "organization", "domains", "applications", "integrations", "team", "billing", "contacts", "settings",
]);
/** What the registrant who has not yet been confirmed as owner needs: the way to get confirmed. */
const SETUP_ITEMS = new Set(["onboarding", "organization"]);

/** The navigation this person should see. A Member sees Overview, Alerts and their own account; a pending owner also
 * sees the two pages that confirm the company; owners and administrators see everything. */
export function visibleNav(groups: NavGroup[], who: NavStanding): NavGroup[] {
  if (who.isAdmin) return groups;
  return groups
    .filter((g) => g.label !== "Administration")
    .map((g) => ({
      ...g,
      items: g.items.filter((i) => !ADMIN_ITEMS.has(i.id) || (who.pendingOwner && SETUP_ITEMS.has(i.id))),
    }))
    .filter((g) => g.items.length > 0);
}
