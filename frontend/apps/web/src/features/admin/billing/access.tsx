"use client";

import type { BillingBundle, BillingGrant } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, EmptyState, Select, TextField, type Column } from "@zoiko/ui";
import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { BILLING_KEY } from "./shared";

export const ACCESS_KEY = [...BILLING_KEY, "access"] as const;

const BUNDLE_COPY: Record<BillingBundle, { label: string; help: string }> = {
  BILLING_ADMIN: { label: "Billing Admin", help: "Everything in billing except giving billing access to others: pay, manage the card, update the billing profile, ask for plan changes, read activity." },
  BILLING_VIEWER: { label: "Billing Viewer", help: "Read only: the overview, invoices and downloads." },
  INVOICE_READER: { label: "Invoice reader", help: "Invoices and their downloads only. No payment method, no checkout." },
};

/** What the signed-in person can do in billing, and for an Owner the grants they have given. The server decides every call; this only
 * decides what the page shows. */
export function AccessSection() {
  const access = useCellQuery(ACCESS_KEY, (api) => api.billing.access());
  return (
    <QueryView query={access}>
      {(a) => {
        const held = new Set(a.permissions);
        const label = new Map(a.catalogue.map((c) => [c.key, c.label]));
        return (
          <div className="space-y-4">
            {!a.enforced ? (
              <Banner tone="warn" title="Billing access is not enforced yet">
                The organization is on the report-only setting, so administrators can still reach billing. Each request that would have been refused is recorded.
              </Banner>
            ) : null}
            <Card title="Your billing access" description="An administrator has no billing access by being one. The Owner has all of it; everyone else has what an Owner gave them.">
              {a.permissions.length ? (
                <ul className="list-disc space-y-1 pl-5 text-sm">
                  {a.permissions.map((k) => (
                    <li key={k}>{label.get(k) ?? k}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted">You do not have billing access. Ask an Owner of the organization to give it to you.</p>
              )}
            </Card>
            {held.has("billing.audit.read") || held.has("billing.access.delegate") ? <Grants canGive={held.has("billing.access.delegate")} /> : null}
          </div>
        );
      }}
    </QueryView>
  );
}

function Grants({ canGive }: { canGive: boolean }) {
  const grants = useCellQuery([...ACCESS_KEY, "grants"], (api) => api.billing.grants());
  const members = useCellQuery(["tenant", "members"], (api) => api.tenant.members(), { enabled: canGive });
  const [who, setWho] = useState("");
  const [bundle, setBundle] = useState<BillingBundle>("BILLING_VIEWER");
  const [reason, setReason] = useState("");
  const [until, setUntil] = useState("");

  const give = useCellMutation(
    (api, v: { grantee: string; bundle: BillingBundle; reason: string; until: string }) =>
      api.billing.giveAccess({
        grantee_principal_id: v.grantee,
        bundle: v.bundle,
        reason: v.reason,
        expires_at: v.until ? new Date(`${v.until}T23:59:59`).toISOString() : null,
      }),
    { invalidate: [ACCESS_KEY], success: "Billing access given.", onSuccess: () => setReason("") },
  );
  const revoke = useCellMutation((api, id: string) => api.billing.revokeAccess(id), {
    invalidate: [ACCESS_KEY],
    success: "Billing access taken away. It ends on the person's next request.",
  });

  const people = (members.data?.items ?? [])
    .filter((m) => m.status === "ACTIVE")
    .map((m) => ({ id: m.principal_id, label: `${m.role.charAt(0)}${m.role.slice(1).toLowerCase()} · ${m.principal_id.slice(0, 8)}` }));
  const valid = who !== "" && reason.trim().length > 0;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (valid) give.mutate({ grantee: who, bundle, reason: reason.trim(), until });
  };

  const columns: Column<BillingGrant>[] = [
    { key: "who", header: "Person", pinned: true, render: (g) => g.grantee_principal_id.slice(0, 8), sortValue: (g) => g.grantee_principal_id },
    { key: "bundle", header: "Access", render: (g) => BUNDLE_COPY[g.bundle].label, sortValue: (g) => g.bundle },
    { key: "reason", header: "Why", render: (g) => g.reason },
    { key: "until", header: "Until", render: (g) => (g.expires_at ? g.expires_at.slice(0, 10) : "No end date"), sortValue: (g) => g.expires_at ?? "" },
    { key: "state", header: "State", render: (g) => (g.revoked_at ? "Taken away" : "Active"), sortValue: (g) => (g.revoked_at ? 1 : 0) },
    ...(canGive
      ? [
          {
            key: "act",
            header: "",
            render: (g: BillingGrant) =>
              g.revoked_at ? null : (
                <Button variant="secondary" onPress={() => revoke.mutate(g.grant_id)} isDisabled={revoke.isPending}>
                  Take away
                </Button>
              ),
          } satisfies Column<BillingGrant>,
        ]
      : []),
  ];

  return (
    <>
      {canGive ? (
        <Card title="Give billing access" description={BUNDLE_COPY[bundle].help}>
          <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
            <Select label="Person" options={[{ id: "", label: "Choose a member" }, ...people]} value={who} onChange={setWho} className="min-w-64" />
            <Select
              label="Access"
              options={(Object.keys(BUNDLE_COPY) as BillingBundle[]).map((id) => ({ id, label: BUNDLE_COPY[id].label }))}
              value={bundle}
              onChange={(v) => setBundle(v as BillingBundle)}
              className="min-w-48"
            />
            <TextField label="Why" value={reason} onChange={setReason} className="min-w-64" />
            <TextField label="Until (optional)" value={until} onChange={setUntil} type="date" className="w-48" />
            <Button type="submit" isDisabled={!valid || give.isPending}>
              Give access
            </Button>
          </form>
          {give.isError ? <ApiErrorView error={give.error} title="Billing access was not given" onRetry={() => give.reset()} /> : null}
        </Card>
      ) : null}
      <QueryView query={grants}>
        {(data) => (
          <Card title="Who has billing access" padded={false}>
            <DataTable<BillingGrant>
              caption="Billing access grants"
              columns={columns}
              rows={data.items}
              rowKey={(g) => g.grant_id}
              empty={<EmptyState title="No one else has billing access" icon={KeyRound}>Only Owners can use billing until an Owner gives access above.</EmptyState>}
            />
            {revoke.isError ? <ApiErrorView error={revoke.error} title="Billing access was not taken away" onRetry={() => revoke.reset()} /> : null}
          </Card>
        )}
      </QueryView>
    </>
  );
}
