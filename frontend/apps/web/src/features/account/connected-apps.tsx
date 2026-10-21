"use client";

import type { ConsentGrant } from "@zoiko/api-client";
import { Button, Card, Code, DataTable, DateTime, Dialog, EmptyState, PageHeader, type Column } from "@zoiko/ui";
import { Link2 } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { scopeDescription } from "@/features/consent/scopes";

const KEY = ["me", "consents"];

function Scopes({ scopes }: { scopes: string[] }) {
  return (
    <ul className="flex flex-wrap gap-1" aria-label="Permissions">
      {scopes.map((s) => (
        <li key={s} title={scopeDescription(s)}>
          <Code>{s}</Code>
        </li>
      ))}
    </ul>
  );
}

export function ConnectedAppsPage() {
  const consents = useCellQuery(KEY, (api) => api.me.consents());
  const [target, setTarget] = useState<ConsentGrant | null>(null);

  const revoke = useCellMutation((api, clientId: string) => api.me.revokeConsent(clientId), {
    invalidate: [KEY],
    success: "Access removed",
    onSuccess: () => setTarget(null),
  });

  const columns: Column<ConsentGrant>[] = [
    {
      key: "app",
      header: "App",
      pinned: true,
      render: (c) => <span className="font-mono text-sm text-ink">{c.client_id}</span>,
      sortValue: (c) => c.client_id,
    },
    { key: "scopes", header: "Permissions", render: (c) => <Scopes scopes={c.scopes} /> },
    { key: "granted", header: "Allowed on", render: (c) => <DateTime value={c.granted_at} />, sortValue: (c) => c.granted_at },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (c) => (
        <Button size="xs" variant="danger-outline" onPress={() => setTarget(c)}>
          Remove access
        </Button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Connected apps"
        description="Third-party apps you have allowed to use your Zoiko iD. Removing access stops the app refreshing its tokens; it will have to ask you again."
      />
      <Card padded={false}>
        <QueryView query={consents}>
          {(data) => (
            <DataTable
              caption="Apps with access to your account"
              columns={columns}
              rows={data.items}
              rowKey={(c) => c.client_id}
              empty={
                <EmptyState icon={Link2} title="No connected apps">
                  When you allow a third-party app to use your Zoiko iD, it appears here.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>

      <Dialog
        isOpen={target !== null}
        onOpenChange={(o) => !o && !revoke.isPending && setTarget(null)}
        title="Remove this app's access?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={revoke.isPending} onPress={() => setTarget(null)}>
              Cancel
            </Button>
            <Button variant="danger" pending={revoke.isPending} onPress={() => target && revoke.mutate(target.client_id)}>
              Remove access
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <p>
            <span className="font-mono text-ink">{target?.client_id}</span> can no longer refresh its access to your account. Access
            tokens it already holds stop working when they expire.
          </p>
          <p>If you use the app again, it will ask for your permission again.</p>
          {revoke.error ? <ApiErrorView error={revoke.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
