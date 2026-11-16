"use client";

import type { ProvisioningSourceItem } from "@zoiko/api-client";
import { Button, Card, CopyId, DataTable, Dialog, EmptyState, PageHeader, RiskBadge, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Play, Plus, Users } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { ConnectionDetails } from "./connection-details";
import { CreateSourceDialog, SOURCES_KEY, SourceError } from "./create-source-dialog";
import { DirectorySyncSection } from "./directory-sync";
import { UserText } from "@/lib/user-text";

export function ProvisioningPage() {
  const sources = useCellQuery(SOURCES_KEY, (api) => api.provisioning.sources());
  const [creating, setCreating] = useState(false);
  const [suspending, setSuspending] = useState<ProvisioningSourceItem | null>(null);

  const suspend = useCellMutation((api, id: string) => api.provisioning.suspendSource(id), {
    invalidate: [SOURCES_KEY],
    success: "Provisioning source suspended",
    onSuccess: () => setSuspending(null),
  });
  const [resuming, setResuming] = useState<ProvisioningSourceItem | null>(null);
  const [approval, setApproval] = useState("");
  const resume = useCellMutation((api, v: { id: string; approval: string }) => api.provisioning.resumeSource(v.id, v.approval.trim() || null), {
    invalidate: [SOURCES_KEY],
    success: "Provisioning source resumed",
    onSuccess: () => closeResume(true),
  });
  function closeResume(force = false) {
    if (resume.isPending && !force) return;
    setResuming(null);
    setApproval("");
    resume.reset();
  }
  const cancelSuspend = () => {
    if (suspend.isPending) return;
    setSuspending(null);
    suspend.reset();
  };

  const columns: Column<ProvisioningSourceItem>[] = [
    { key: "name", header: "Source", pinned: true, render: (s) => <span className="font-medium"><UserText>{s.display_name}</UserText></span>, sortValue: (s) => s.display_name },
    { key: "status", header: "Status", render: (s) => <StatusBadge state={s.status} kind="Source status" />, sortValue: (s) => s.status },
    { key: "client", header: "OAuth client", render: (s) => <CopyId value={s.client_id} label="client ID" />, sortValue: (s) => s.client_id },
    { key: "id", header: "Source ID", render: (s) => <CopyId value={s.source_id} label="source ID" /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (s) =>
        s.status === "ACTIVE" ? (
          <Button size="xs" variant="danger-outline" onPress={() => setSuspending(s)}>
            Suspend
          </Button>
        ) : s.status === "SUSPENDED" ? (
          <Button size="xs" variant="secondary" icon={<Play aria-hidden className="size-3.5" />} onPress={() => setResuming(s)}>
            Resume
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <PageHeader
        title="Provisioning"
        description="SCIM 2.0 sources that create, update and deprovision users and groups from your HR system or identity provider."
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            Register source
          </Button>
        }
      />
      <div className="space-y-5">
        <Card padded={false}>
          <QueryView query={sources}>
            {(data) => (
              <DataTable
                caption="Provisioning sources"
                columns={columns}
                rows={data.items}
                rowKey={(s) => s.source_id}
                empty={
                  <EmptyState
                    icon={Users}
                    title="No provisioning sources"
                    action={
                      <Button size="sm" onPress={() => setCreating(true)}>
                        Register source
                      </Button>
                    }
                  >
                    Register a source to let an identity provider manage accounts through SCIM.
                  </EmptyState>
                }
              />
            )}
          </QueryView>
        </Card>
        <DirectorySyncSection sources={sources.data?.items ?? []} />
        <Card title="Connecting a SCIM client" description="Every source uses the same endpoint; the OAuth client decides which source it acts for.">
          <ConnectionDetails />
        </Card>
      </div>

      <CreateSourceDialog isOpen={creating} onClose={() => setCreating(false)} />

      <Dialog
        isOpen={suspending !== null}
        onOpenChange={(o) => !o && cancelSuspend()}
        title="Suspend this provisioning source?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={suspend.isPending} onPress={cancelSuspend}>
              Cancel
            </Button>
            <Button variant="danger" pending={suspend.isPending} onPress={() => suspending && suspend.mutate(suspending.source_id)}>
              Suspend source
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <RiskBadge risk="R3" />
          <p>
            <strong className="text-ink"><UserText>{suspending?.display_name}</UserText></strong> stops at once: its SCIM requests are refused. Accounts it already
            provisioned stay as they are. You can resume it later; its OAuth client stays bound to it.
          </p>
          {suspend.error ? <ApiErrorView error={suspend.error} /> : null}
        </div>
      </Dialog>

      <Dialog
        isOpen={resuming !== null}
        onOpenChange={(o) => !o && closeResume()}
        title="Resume this provisioning source?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={resume.isPending} onPress={() => closeResume()}>
              Cancel
            </Button>
            <Button pending={resume.isPending} onPress={() => resuming && resume.mutate({ id: resuming.source_id, approval })}>
              Resume source
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <RiskBadge risk="R3" />
          <p>
            <strong className="text-ink"><UserText>{resuming?.display_name}</UserText></strong> can create, change and suspend its accounts again as soon as it is resumed. It needs a
            fresh passkey check, room for another source in your plan, and an active OAuth client.
          </p>
          <TextField
            label="Approval request ID (optional)"
            value={approval}
            onChange={setApproval}
            mono
            description="Needed when tenant policy requires a second administrator. The approval binds to the change “resume”."
          />
          {resume.error ? <SourceError error={resume.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
