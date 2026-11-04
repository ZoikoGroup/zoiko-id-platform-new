import type { BillingGrant, CommitmentDelegate, MyPermissions, RoleMatrix } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { actionsFor, fromBillingGrant, fromCommitmentDelegate, myCounts, register, summarise } from "./roles-logic";

const matrix: RoleMatrix = {
  version: "1",
  policy_hash: "h",
  roles: ["OWNER", "VIEWER"],
  matrices: [
    {
      matrix: "A",
      title: "Administration",
      actions: [
        { action: "a.invite", feature: "Invite", permissions: { OWNER: "X", VIEWER: "-" }, rule: "" },
        { action: "a.read", feature: "Read members", permissions: { OWNER: "R", VIEWER: "R" }, rule: "" },
        { action: "a.approve", feature: "Approve role", permissions: { OWNER: "A", VIEWER: "-" }, rule: "" },
      ],
    },
    { matrix: "B", title: "Billing", actions: [{ action: "b.pay", feature: "Pay", permissions: { OWNER: "X" }, rule: "" }] },
  ],
};

describe("role gallery", () => {
  it("counts what each role can do across every matrix, and treats a missing cell as no access", () => {
    const [owner, viewer] = summarise(matrix);
    expect(owner!.counts).toMatchObject({ X: 2, A: 1, R: 1 });
    expect(owner!.total).toBe(4);
    expect(viewer!.counts).toMatchObject({ R: 1, "-": 3 });
    expect(viewer!.total).toBe(1);
  });

  it("lists a role's actions by matrix, strongest first, and leaves out the ones it has no right to", () => {
    const g = actionsFor(matrix, "OWNER");
    expect(g.map((x) => x.matrix)).toEqual(["A", "B"]);
    expect(g[0]!.rows.map((r) => r.action)).toEqual(["a.invite", "a.approve", "a.read"]);
    expect(actionsFor(matrix, "VIEWER")).toHaveLength(1);
    expect(actionsFor(matrix, "NOBODY")).toEqual([]);
  });

  it("counts a person's rights by their strongest mode", () => {
    const p: MyPermissions = { roles: ["OWNER"], policy_hash: "h", enforcement: false, rights: { x: ["R", "X"], y: ["S"], z: ["R"] } };
    expect(myCounts(p)).toMatchObject({ X: 1, S: 1, R: 1 });
  });
});

describe("delegation register", () => {
  const NOW = Date.parse("2026-11-04T10:00:00Z");
  const grant = (o: Partial<BillingGrant>): BillingGrant => ({
    grant_id: "g",
    grantee_principal_id: "p",
    bundle: "BILLING_VIEWER",
    permissions: [],
    reason: "audit",
    granted_by: "o",
    expires_at: null,
    created_at: "x",
    revoked_at: null,
    ...o,
  });
  const delegate = (o: Partial<CommitmentDelegate>): CommitmentDelegate => ({
    delegate_id: "d",
    principal_id: "p",
    kind: "BILLING_SPEND",
    cap_usd: "5000.00",
    effective_from: "x",
    effective_to: null,
    revoked_at: null,
    ...o,
  });

  it("puts what ends soon first, then active, then ended and taken away", () => {
    const rows = register(
      [
        fromBillingGrant(grant({ grant_id: "revoked", revoked_at: "2026-11-01T00:00:00Z" })),
        fromBillingGrant(grant({ grant_id: "expired", expires_at: "2026-11-01T00:00:00Z" })),
        fromBillingGrant(grant({ grant_id: "open" })),
        fromBillingGrant(grant({ grant_id: "soon", expires_at: "2026-11-08T00:00:00Z" })),
      ],
      NOW,
    );
    expect(rows.map((r) => [r.id, r.state])).toEqual([
      ["soon", "ENDING_SOON"],
      ["open", "ACTIVE"],
      ["expired", "EXPIRED"],
      ["revoked", "REVOKED"],
    ]);
  });

  it("words a commitment limit with its cap", () => {
    expect(fromCommitmentDelegate(delegate({})).what).toBe("Commitment signing limit up to 5000.00 USD");
    expect(fromCommitmentDelegate(delegate({ kind: "PROCUREMENT_APPROVER" })).what).toMatch(/^Procurement approver/);
  });
});
