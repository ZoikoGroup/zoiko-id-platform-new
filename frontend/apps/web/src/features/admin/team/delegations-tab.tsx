"use client";

import { Badge, Banner, Button, Card, DataTable, EmptyState, type Column } from "@zoiko/ui";
import { KeyRound } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { delegateRevokeStepUp } from "../billing/commitment-logic";
import { DELEGATION_STATE, NEVER_CONFERRED, fromBillingGrant, fromCommitmentDelegate, register, type Delegation } from "./roles-logic";

/** C00 Delegations: one register of what has been handed out (billing access and commitment limits), with who gave it, why, when it ends and
 * a way to take it away. Taking away is immediate. Giving a new one is done where it is used, in Billing, so its checks stay in one place. */
export function DelegationsTab() {
  const access = useCellQuery(["billing", "access"], (api) => api.billing.access());
  const perms = new Set(access.data?.permissions ?? []);
  const canSee = perms.has("billing.audit.read") || perms.has("billing.access.delegate");
  const canRevoke = perms.has("billing.access.delegate");
  return (
    <div className="space-y-4">
      <Banner tone="info" title="Delegation is never a wildcard">
        Each one names a person, what it covers, why, and when it ends. Widening one is a new grant. Taking one away ends it on the person&apos;s next request. {NEVER_CONFERRED}
      </Banner>
      {access.isLoading ? null : canSee ? (
        <Register canRevoke={canRevoke} />
      ) : (
        <Banner tone="warn" title="You cannot see delegations">
          Seeing who has been given billing access or a commitment limit needs billing audit access, which an Owner can give you.
        </Banner>
      )}
      <p className="text-sm text-ink-muted">
        To give billing access or a commitment limit, use <Link className="font-medium text-brand-700 underline" href="/admin/billing">Billing</Link>: the Billing access and Commitments tabs.
      </p>
    </div>
  );
}

function Register({ canRevoke }: { canRevoke: boolean }) {
  const grants = useCellQuery(["billing", "access", "grants"], (api) => api.billing.grants());
  const delegates = useCellQuery(["billing", "commitments", "delegates"], (api) => api.billing.delegates());
  const tenant = useCellQuery(["tenant", "me"], (api) => api.tenant.me(), { enabled: canRevoke });
  const { proveAction } = useRuntime();
  const [now] = useState(() => Date.now());

  const revoke = useCellMutation(
    async (api, d: Delegation) => {
      if (d.kind === "BILLING_ACCESS") return api.billing.revokeAccess(d.id);
      if (!tenant.data) throw new Error("The organization could not be read. Try again.");
      const proof = await proveAction(delegateRevokeStepUp(tenant.data.tenant_id, d.id));
      return api.billing.revokeDelegate(d.id, proof);
    },
    { invalidate: [["billing"]], success: "Taken away. It ends on the person's next request." },
  );

  const items = [...(grants.data?.items ?? []).map(fromBillingGrant), ...(delegates.data?.items ?? []).map(fromCommitmentDelegate)];
  const rows = register(items, now);
  const columns: Column<(typeof rows)[number]>[] = [
    { key: "who", header: "Person", pinned: true, render: (d) => <span className="font-mono text-xs">{d.principal.slice(0, 8)}</span>, sortValue: (d) => d.principal },
    { key: "what", header: "What it covers", render: (d) => d.what },
    { key: "why", header: "Why", render: (d) => <span className="text-sm text-ink-muted">{d.why}</span> },
    { key: "by", header: "Given by", render: (d) => (d.by ? <span className="font-mono text-xs">{d.by.slice(0, 8)}</span> : "An Owner") },
    { key: "until", header: "Ends", render: (d) => (d.until ? d.until.slice(0, 10) : "No end date"), sortValue: (d) => d.until ?? "9" },
    { key: "state", header: "State", render: (d) => <Badge tone={DELEGATION_STATE[d.state].tone}>{DELEGATION_STATE[d.state].label}</Badge>, sortValue: (d) => d.state },
    ...(canRevoke
      ? [
          {
            key: "act",
            header: "",
            render: (d: (typeof rows)[number]) =>
              d.state === "ACTIVE" || d.state === "ENDING_SOON" ? (
                <Button size="xs" variant="secondary" isDisabled={revoke.isPending} onPress={() => revoke.mutate(d)}>
                  Take away
                </Button>
              ) : null,
          } satisfies Column<(typeof rows)[number]>,
        ]
      : []),
  ];

  return (
    <QueryView query={grants}>
      {() => (
        <Card title="Delegation register" description="Billing access and commitment limits, newest concerns first. Ended and revoked ones stay here as evidence." padded={false}>
          <DataTable
            caption="Delegations"
            columns={columns}
            rows={rows}
            rowKey={(d) => `${d.kind}-${d.id}`}
            empty={<EmptyState title="Nothing has been delegated" icon={KeyRound}>Only Owners can use billing until an Owner gives access.</EmptyState>}
          />
          {revoke.isError ? <ApiErrorView error={revoke.error} title="It was not taken away" onRetry={() => revoke.reset()} /> : null}
        </Card>
      )}
    </QueryView>
  );
}
