"use client";

import type { JitGrant } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, Dialog, EmptyState, PageHeader, TextField } from "@zoiko/ui";
import { Stamp } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { GRANTS_KEY, grantColumns } from "./shared";

export function ApprovalsPage() {
  const pending = useCellQuery([...GRANTS_KEY, "pending"], (api) => api.requests("pending"));
  const active = useCellQuery([...GRANTS_KEY, "active"], (api) => api.requests("all", "ACTIVE"));
  const [denying, setDenying] = useState<JitGrant | null>(null);
  const [revoking, setRevoking] = useState<JitGrant | null>(null);
  const [reason, setReason] = useState("");
  const approve = useCellMutation((api, g: JitGrant) => api.approve(g.jit_grant_id), {
    invalidate: [GRANTS_KEY],
    success: (g) => (g.state === "APPROVED" ? "Approved: the requester can activate it" : "Approval recorded: a second approver is still required"),
  });
  const deny = useCellMutation((api, g: JitGrant) => api.deny(g.jit_grant_id, { reason: reason.trim() || "denied" }), {
    invalidate: [GRANTS_KEY],
    success: "Request denied",
    onSuccess: () => {
      setDenying(null);
      setReason("");
    },
  });
  const revoke = useCellMutation((api, g: JitGrant) => api.revoke(g.jit_grant_id, { reason: reason.trim() || "revoked by approver" }), {
    invalidate: [GRANTS_KEY],
    success: "Access revoked",
    onSuccess: () => {
      setRevoking(null);
      setReason("");
    },
  });

  const queueColumns = grantColumns([
    {
      key: "reason",
      header: "Reason",
      render: (g) => (
        <span className="block max-w-72 truncate text-xs" title={g.reason}>
          {g.reason}
        </span>
      ),
    },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (g) => (
        <span className="flex justify-end gap-1.5">
          <Button size="xs" pending={approve.isPending} onPress={() => approve.mutate(g)}>
            Approve
          </Button>
          <Button size="xs" variant="danger-outline" onPress={() => setDenying(g)}>
            Deny
          </Button>
        </span>
      ),
    },
  ]);
  const activeColumns = grantColumns([
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (g) => (
        <Button size="xs" variant="danger-outline" onPress={() => setRevoking(g)}>
          Revoke
        </Button>
      ),
    },
  ]);

  const target = denying ?? revoking;
  const mutation = denying ? deny : revoke;
  return (
    <>
      <PageHeader
        title="Approvals"
        description="Independent approval: you can never approve your own request, the same approver never counts twice, and R3 / key / evidence / export capabilities need two people."
      />
      <div className="space-y-5">
        {approve.error ? <ApiErrorView error={approve.error} /> : null}
        <Card padded={false} title="Waiting for approval">
          <QueryView query={pending}>
            {(d) => (
              <DataTable caption="Pending JIT requests" columns={queueColumns} rows={d.items} rowKey={(g) => g.jit_grant_id} empty={<EmptyState icon={Stamp} title="Nothing to approve" />} />
            )}
          </QueryView>
        </Card>
        <Card padded={false} title="Active operator access" description="Any approver can take active access away; it ends immediately.">
          <QueryView query={active}>
            {(d) => <DataTable caption="Active grants" columns={activeColumns} rows={d.items} rowKey={(g) => g.jit_grant_id} empty={<EmptyState icon={Stamp} title="No active operator access" />} />}
          </QueryView>
        </Card>
      </div>
      <Dialog
        isOpen={target !== null}
        onOpenChange={(o) => {
          if (!o) {
            setDenying(null);
            setRevoking(null);
          }
        }}
        title={denying ? "Deny this request?" : "Revoke this access?"}
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button
              variant="secondary"
              onPress={() => {
                setDenying(null);
                setRevoking(null);
              }}
            >
              Cancel
            </Button>
            <Button variant="danger" pending={mutation.isPending} onPress={() => target && mutation.mutate(target)}>
              {denying ? "Deny" : "Revoke"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Banner tone="info">{target?.capability} on {target?.resource_scope}</Banner>
          <TextField label="Reason" value={reason} onChange={setReason} />
          {mutation.error ? <ApiErrorView error={mutation.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
