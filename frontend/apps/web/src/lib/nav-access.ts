import type { NavGroup } from "@zoiko/ui";

/** What the signed-in person is to the company (see `standing` in the onboarding feature). */
export type NavStanding = {
  isAdmin: boolean;
  pendingOwner: boolean;
  billingAccess?: boolean;
  /** What the person's roles could do, from the role matrix (`GET /v1/tenant/me/permissions`): matrix action id -> modes (X, S, A, R, T). */
  rights?: Record<string, string[]>;
};

/** The matrix actions that make a page worth showing. A page appears when the person's roles hold any of them (ZID-ENG-DEC-007).
 * Approver: votes, so Approvals. Operator: runs verification and credential work. Viewer: reads evidence. A plain Member holds
 * none of these, so keeps the short menu. Navigation only: the cell still decides each request. */
export const PAGE_RIGHTS: Record<string, string[]> = {
  approvals: ["d.approve-eligible-pending-request", "a.approve-privileged-role-grant", "f.approve-sensitive-evidence-export"],
  verification: ["b.upload-organization-kyb-evidence", "b.submit-refresh-organization-verification", "b.review-organization-evidence-internally"],
  credentials: ["b.draft-routine-credential-issuance", "b.authorize-high-trust-credential-issuance", "b.revoke-ordinary-scoped-credential"],
  authority: ["d.create-scoped-delegation-request", "d.approve-authority-delegation"],
  mandates: ["d.create-scoped-delegation-request", "d.approve-authority-delegation"],
  policies: ["d.propose-approval-workflow-policies"],
  decisions: ["d.approve-eligible-pending-request", "d.execute-ordinary-approved-operation"],
  signals: ["c.view-organization-security-posture"],
  evidence: ["f.view-authorized-audit-and-ledger-entries"],
  agents: ["e.request-scoped-ai-agent-capability-grant", "e.approve-agent-authority-sensitive-tool-grant"],
  developers: ["e.inspect-assigned-api-event-metadata", "e.rotate-assigned-nonprivileged-api-credential"],
  integrations: ["e.view-integration-directory-status"],
  team: ["a.list-active-members-and-assigned-roles"],
};

/** True when the roles hold the action in one of the modes (all modes when none is given). */
export function can(rights: Record<string, string[]> | undefined, action: string, ...modes: string[]): boolean {
  const held = rights?.[action];
  return Boolean(held && (modes.length === 0 || modes.some((m) => held.includes(m))));
}

function pageAllowed(id: string, rights: Record<string, string[]> | undefined): boolean {
  return (PAGE_RIGHTS[id] ?? []).some((a) => can(rights, a));
}

/** Company administration. A person without an administrator role is not shown these controls (UX-02): the cell still
 * refuses the calls by itself (UX-03), so hiding is courtesy, never the protection. */
const ADMIN_ITEMS = new Set([
  "onboarding", "organization", "setup", "domains", "applications", "integrations", "team", "approvals", "billing", "contacts", "checks", "settings",
]);
/** What the registrant who has not yet been confirmed as owner needs: the way to get confirmed. */
const SETUP_ITEMS = new Set(["onboarding", "organization", "setup"]);

/** The navigation this person should see. A Member sees Overview, Alerts and their own account; a pending owner also
 * sees the two pages that confirm the company; a person the Owner gave billing access sees Billing (the page then shows only the tabs they may use); owners and administrators see everything. */
export function visibleNav(groups: NavGroup[], who: NavStanding): NavGroup[] {
  if (who.isAdmin) return groups;
  return groups
    .map((g) => ({
      ...g,
      items: g.items.filter(
        (i) => (g.label !== "Administration" && !ADMIN_ITEMS.has(i.id) && !(i.id in PAGE_RIGHTS)) || (who.pendingOwner && SETUP_ITEMS.has(i.id)) || (who.billingAccess === true && i.id === "billing") || pageAllowed(i.id, who.rights),
      ),
    }))
    .filter((g) => g.items.length > 0);
}
