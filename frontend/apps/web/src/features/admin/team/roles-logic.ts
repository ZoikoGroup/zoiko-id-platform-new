import type { BillingBundle, BillingGrant, CommitmentDelegate, MyPermissions, RoleMatrix, RoleMatrixRow, RoleMode } from "@zoiko/api-client";

/** The Team and roles screen (ZID-UX-COM-010, C00). The role gallery reads the published matrix from the server, so it never claims more
 * than the policy says; it is a catalogue for navigation and the server decides every request. */

export const MODE_LABEL: Record<RoleMode, string> = {
  X: "Can do it",
  S: "Can prepare or ask for it",
  A: "Can approve it, when independent",
  R: "Can read it",
  T: "Own account only",
  "-": "No access",
};

export const MODE_SHORT: Record<RoleMode, string> = { X: "Do", S: "Prepare", A: "Approve", R: "Read", T: "Own", "-": "—" };

export const ROLE_COPY: Record<string, { label: string; help: string }> = {
  OWNER: { label: "Owner", help: "Holds the organization. Gives billing access, changes owners and approves the highest-impact steps." },
  ADMIN: { label: "Administrator", help: "Runs day-to-day administration. Has no billing, privacy or finance authority by being an administrator." },
  APPROVER: { label: "Approver", help: "Independently approves changes that another person asked for. Cannot approve their own request." },
  OPERATOR: { label: "Operator", help: "Runs the assigned verification and credential work. No policy or role administration." },
  VIEWER: { label: "Viewer", help: "Reads redacted views only. Cannot change anything." },
};
export const roleCopy = (r: string) => ROLE_COPY[r] ?? { label: r.charAt(0) + r.slice(1).toLowerCase(), help: "" };

export const BUNDLE_COPY: Record<BillingBundle, { label: string; help: string }> = {
  BILLING_ADMIN: { label: "Billing Admin", help: "Everything in billing except giving billing access to others. Given by an Owner, with a reason and an optional end date." },
  BILLING_VIEWER: { label: "Billing Viewer", help: "Read only: the overview, invoices and downloads." },
  INVOICE_READER: { label: "Invoice reader", help: "Invoices and their downloads only. No payment method, no checkout." },
};

/** Things that no customer role or grant ever confers, said once and plainly. */
export const NEVER_CONFERRED =
  "No customer role, billing grant or approval limit gives anyone Zoiko's own authority: discounts, credits, pilots, refunds, disputes or accounting.";

export type RoleSummary = { role: string; counts: Record<RoleMode, number>; total: number };

/** How many things each role can do, prepare, approve, read or do on its own account, across every published matrix. */
export function summarise(m: RoleMatrix): RoleSummary[] {
  return m.roles.map((role) => {
    const counts: Record<RoleMode, number> = { X: 0, S: 0, A: 0, R: 0, T: 0, "-": 0 };
    for (const mx of m.matrices) for (const row of mx.actions) counts[row.permissions[role] ?? "-"] += 1;
    return { role, counts, total: counts.X + counts.S + counts.A + counts.R + counts.T };
  });
}

export type RoleActions = { matrix: string; title: string; rows: (RoleMatrixRow & { mode: RoleMode })[] }[];

/** The actions a role has any right to, grouped by matrix, most powerful mode first. */
export function actionsFor(m: RoleMatrix, role: string): RoleActions {
  const order: RoleMode[] = ["X", "A", "S", "R", "T"];
  return m.matrices
    .map((mx) => ({
      matrix: mx.matrix,
      title: mx.title,
      rows: mx.actions
        .map((r) => ({ ...r, mode: (r.permissions[role] ?? "-") as RoleMode }))
        .filter((r) => r.mode !== "-")
        .sort((a, b) => order.indexOf(a.mode) - order.indexOf(b.mode) || a.feature.localeCompare(b.feature)),
    }))
    .filter((g) => g.rows.length > 0);
}

/** What the signed-in person's own roles can do: the count by mode, for the "Your access" card. */
export function myCounts(p: MyPermissions): Record<RoleMode, number> {
  const out: Record<RoleMode, number> = { X: 0, S: 0, A: 0, R: 0, T: 0, "-": 0 };
  for (const modes of Object.values(p.rights)) {
    const best = (["X", "A", "S", "R", "T"] as RoleMode[]).find((x) => modes.includes(x));
    if (best) out[best] += 1;
  }
  return out;
}

export type Delegation =
  | { kind: "BILLING_ACCESS"; id: string; principal: string; what: string; why: string; until: string | null; revoked: boolean; by: string }
  | { kind: "COMMITMENT_LIMIT"; id: string; principal: string; what: string; why: string; until: string | null; revoked: boolean; by: string };

export function fromBillingGrant(g: BillingGrant): Delegation {
  return {
    kind: "BILLING_ACCESS",
    id: g.grant_id,
    principal: g.grantee_principal_id,
    what: BUNDLE_COPY[g.bundle].label,
    why: g.reason,
    until: g.expires_at,
    revoked: g.revoked_at !== null,
    by: g.granted_by,
  };
}

export function fromCommitmentDelegate(d: CommitmentDelegate): Delegation {
  return {
    kind: "COMMITMENT_LIMIT",
    id: d.delegate_id,
    principal: d.principal_id,
    what: `${d.kind === "BILLING_SPEND" ? "Commitment signing limit" : "Procurement approver"} up to ${d.cap_usd} USD`,
    why: "Signs commitments within this limit; a separate approval is still needed for each purchase.",
    until: d.effective_to,
    revoked: d.revoked_at !== null,
    by: "",
  };
}

/** Active first, then by soonest end; a lapsed one is shown as ended, not hidden, so the evidence stays visible. */
export function register(items: Delegation[], nowMs: number): (Delegation & { state: "ACTIVE" | "ENDING_SOON" | "EXPIRED" | "REVOKED" })[] {
  const tagged = items.map((d) => {
    const end = d.until ? Date.parse(d.until) : null;
    const state = d.revoked ? ("REVOKED" as const) : end !== null && end <= nowMs ? ("EXPIRED" as const) : end !== null && end - nowMs <= 7 * 86_400_000 ? ("ENDING_SOON" as const) : ("ACTIVE" as const);
    return { ...d, state };
  });
  const rank = { ENDING_SOON: 0, ACTIVE: 1, EXPIRED: 2, REVOKED: 3 } as const;
  return tagged.sort((a, b) => rank[a.state] - rank[b.state] || (a.until ?? "9").localeCompare(b.until ?? "9"));
}

export const DELEGATION_STATE: Record<"ACTIVE" | "ENDING_SOON" | "EXPIRED" | "REVOKED", { label: string; tone: "ok" | "warn" | "neutral" | "danger" }> = {
  ACTIVE: { label: "Active", tone: "ok" },
  ENDING_SOON: { label: "Ends within a week", tone: "warn" },
  EXPIRED: { label: "Ended", tone: "neutral" },
  REVOKED: { label: "Taken away", tone: "danger" },
};
