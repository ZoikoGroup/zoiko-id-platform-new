"use client";

import type { JitGrant } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, Dialog, EmptyState, PageHeader, TextField } from "@zoiko/ui";
import { KeyRound, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { AccessRequestDialog, GRANTS_KEY, grantColumns, useCapabilities } from "./shared";

export function MyAccessPage() {
  const grants = useCellQuery([...GRANTS_KEY, "mine"], (api) => api.requests("mine"));
  const caps = useCapabilities();
  const [requesting, setRequesting] = useState(false);
  const [revoking, setRevoking] = useState<JitGrant | null>(null);
  const [reason, setReason] = useState("");
  const activate = useCellMutation((api, g: JitGrant) => api.activate(g.jit_grant_id), {
    invalidate: [GRANTS_KEY],
    success: (g) => `Access active until ${g.expires_at ? new Date(g.expires_at).toLocaleTimeString() : "its limit"}`,
  });
  const cancel = useCellMutation((api, g: JitGrant) => api.cancel(g.jit_grant_id), { invalidate: [GRANTS_KEY], success: "Request cancelled" });
  const revoke = useCellMutation((api, g: JitGrant) => api.revoke(g.jit_grant_id, { reason: reason.trim() || "work complete" }), {
    invalidate: [GRANTS_KEY],
    success: "Access given up",
    onSuccess: () => {
      setRevoking(null);
      setReason("");
    },
  });

  const columns = grantColumns([
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (g) =>
        g.state === "APPROVED" ? (
          <span className="flex justify-end gap-1.5">
            <Button size="xs" pending={activate.isPending} onPress={() => activate.mutate(g)}>
              Activate
            </Button>
            <Button size="xs" variant="secondary" onPress={() => cancel.mutate(g)}>
              Cancel
            </Button>
          </span>
        ) : g.state === "REQUESTED" ? (
          <Button size="xs" variant="secondary" pending={cancel.isPending} onPress={() => cancel.mutate(g)}>
            Cancel
          </Button>
        ) : g.state === "ACTIVE" ? (
          <Button size="xs" variant="danger-outline" onPress={() => setRevoking(g)}>
            End now
          </Button>
        ) : null,
    },
  ]);

  return (
    <>
      <PageHeader
        title="My access"
        description="You hold no standing production privilege. Request exactly the capability, scope and time you need for a case."
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setRequesting(true)}>
            Request access
          </Button>
        }
      />
      <div className="space-y-5">
        {activate.error ? <ApiErrorView error={activate.error} /> : null}
        <Card padded={false} title="Requests and grants">
          <QueryView query={grants}>
            {(d) => (
              <DataTable
                caption="My JIT requests and grants"
                columns={columns}
                rows={d.items}
                rowKey={(g) => g.jit_grant_id}
                empty={
                  <EmptyState icon={KeyRound} title="No access requests" action={<Button size="sm" onPress={() => setRequesting(true)}>Request access</Button>}>
                    Request access for a support or incident case when you need it.
                  </EmptyState>
                }
              />
            )}
          </QueryView>
        </Card>
        <QueryView query={caps}>
          {(c) => (
            <Card title="Separation of duties" description="You may hold at most one capability of each set at a time, and never approve a request that conflicts with your own access.">
              <ul className="space-y-2 text-sm">
                {c.separation_of_duties.map((r) => (
                  <li key={r.rule}>
                    <span className="font-mono text-xs">{r.capabilities.join(" · ")}</span>
                    <span className="block text-ink-muted">{r.rationale}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </QueryView>
      </div>
      <AccessRequestDialog isOpen={requesting} onClose={() => setRequesting(false)} />
      <Dialog
        isOpen={revoking !== null}
        onOpenChange={(o) => !o && setRevoking(null)}
        title="End this access now?"
        size="sm"
        footer={
          <>
            <Button variant="secondary" onPress={() => setRevoking(null)}>
              Keep
            </Button>
            <Button variant="danger" pending={revoke.isPending} onPress={() => revoking && revoke.mutate(revoking)}>
              End access
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Banner tone="info">Access ends at once. Requesting it again needs a new approval.</Banner>
          <TextField label="Reason" value={reason} onChange={setReason} placeholder="work complete" />
          {revoke.error ? <ApiErrorView error={revoke.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
