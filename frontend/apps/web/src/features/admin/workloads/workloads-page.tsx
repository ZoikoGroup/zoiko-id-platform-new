"use client";

import type { WorkloadBindingView } from "@zoiko/api-client";
import { Banner, Button, Card, CopyId, DataTable, DateTime, Dialog, EmptyState, PageHeader, RiskBadge, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Plus, ServerCog, ShieldAlert } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const WL_KEY = ["admin", "workloads"];

export function WorkloadsPage() {
  const trust = useCellQuery([...WL_KEY, "trust"], (api) => api.workloads.trustDomain());
  const bindings = useCellQuery(WL_KEY, (api) => api.workloads.bindings());
  const [registering, setRegistering] = useState(false);
  const [revoking, setRevoking] = useState<WorkloadBindingView | null>(null);
  const [env, setEnv] = useState("");
  const [reason, setReason] = useState("COMPROMISE_SUSPECTED");

  const revoke = useCellMutation((api, b: WorkloadBindingView) => api.workloads.revoke({ spiffe_id: b.spiffe_id, reason }), {
    invalidate: [WL_KEY],
    success: "Binding revoked: the workload is refused from its next request",
    onSuccess: () => setRevoking(null),
  });
  const quarantine = useCellMutation((api) => api.workloads.quarantine(env.trim(), { reason }), {
    invalidate: [WL_KEY],
    success: (r) => `Environment ${r.environment_id} quarantined (security revision ${r.security_revision})`,
  });
  const release = useCellMutation((api) => api.workloads.release(env.trim()), { invalidate: [WL_KEY], success: "Environment released" });

  const columns: Column<WorkloadBindingView>[] = [
    { key: "spiffe", header: "SPIFFE ID", pinned: true, render: (b) => <span className="font-mono text-xs">{b.spiffe_id}</span>, sortValue: (b) => b.spiffe_id },
    { key: "status", header: "Status", render: (b) => <StatusBadge state={b.status} kind="Workload binding" /> },
    { key: "principal", header: "Acts as", render: (b) => <CopyId value={b.logical_principal_id} label="principal ID" /> },
    { key: "env", header: "Environment", render: (b) => <span className="font-mono text-xs">{b.environment_id}</span> },
    { key: "attest", header: "Attestation", render: (b) => `ATTEST-${b.attestation_required}` },
    { key: "rev", header: "Security revision", render: (b) => <span className="tabular">{b.security_revision}</span> },
    { key: "until", header: "Valid until", render: (b) => <DateTime value={b.valid_to} /> },
    { key: "digest", header: "Artifact", render: (b) => <CopyId value={b.artifact_digest} label="artifact digest" />, defaultHidden: true },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (b) =>
        b.status === "ACTIVE" ? (
          <Button size="xs" variant="danger-outline" onPress={() => setRevoking(b)}>
            Revoke
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <PageHeader
        title="Workload identity"
        description="SPIFFE identities authenticate workloads; a registered binding decides which logical principal one may act as. An identity alone authorizes nothing."
      />
      <div className="space-y-5">
        <QueryView query={trust}>
          {(t) =>
            t.trust_domain ? (
              <Banner tone="info" title={`Trust domain ${t.trust_domain}`}>
                {t.federated.length ? `Federated: ${t.federated.join(", ")}.` : "No federated trust domains: no trust-on-first-use."}
              </Banner>
            ) : (
              <Banner tone="warn" title="No trust domain configured for this cell" />
            )
          }
        </QueryView>
        <Card
          padded={false}
          title="Workload bindings"
          actions={
            <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setRegistering(true)}>
              Register binding
            </Button>
          }
        >
          <QueryView query={bindings}>
            {(d) => (
              <DataTable
                caption="Workload bindings"
                columns={columns}
                rows={d.items}
                rowKey={(b) => b.binding_id}
                empty={<EmptyState icon={ServerCog} title="No workload bindings">An unregistered workload identity maps to nothing.</EmptyState>}
              />
            )}
          </QueryView>
        </Card>
        <Card title="Environment quarantine" description="Refuses every workload of an environment from the next request on and bumps its security revision (the R3 revocation path).">
          <div className="grid gap-3 sm:grid-cols-4 sm:items-end">
            <TextField label="Environment" value={env} onChange={setEnv} mono />
            <TextField label="Reason code" value={reason} onChange={(v) => setReason(v.toUpperCase())} mono />
            <Button size="sm" variant="danger" icon={<ShieldAlert aria-hidden className="size-4" />} pending={quarantine.isPending} isDisabled={!env} onPress={() => quarantine.mutate(undefined)}>
              Quarantine
            </Button>
            <Button size="sm" variant="secondary" pending={release.isPending} isDisabled={!env} onPress={() => release.mutate(undefined)}>
              Release
            </Button>
          </div>
          {quarantine.error ? <ApiErrorView error={quarantine.error} /> : null}
          {release.error ? <ApiErrorView error={release.error} /> : null}
        </Card>
      </div>
      <RegisterDialog isOpen={registering} onClose={() => setRegistering(false)} />
      <Dialog
        isOpen={revoking !== null}
        onOpenChange={(o) => !o && setRevoking(null)}
        title="Revoke this workload binding?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" onPress={() => setRevoking(null)}>
              Cancel
            </Button>
            <Button variant="danger" pending={revoke.isPending} onPress={() => revoking && revoke.mutate(revoking)}>
              Revoke
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-muted">{revoking?.spiffe_id} can no longer act as its principal. Registering it again is a new governed binding.</p>
        {revoke.error ? <ApiErrorView error={revoke.error} /> : null}
      </Dialog>
    </>
  );
}

function RegisterDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [agent, setAgent] = useState("");
  const [spiffe, setSpiffe] = useState("");
  const [version, setVersion] = useState("");
  const [digest, setDigest] = useState("");
  const [env, setEnv] = useState("prod");
  const [attest, setAttest] = useState("2");
  const [until, setUntil] = useState("");
  const register = useCellMutation(
    (api) =>
      api.workloads.register({
        agent_principal_id: agent.trim(),
        spiffe_id: spiffe.trim(),
        blueprint_version: version.trim(),
        artifact_digest: digest.trim(),
        environment_id: env.trim(),
        attestation_required: Number(attest),
        valid_to: new Date(until).toISOString(),
      }),
    { invalidate: [WL_KEY], success: "Workload binding registered", onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Register a workload binding"
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={register.isPending} isDisabled={!agent || !spiffe.startsWith("spiffe://") || !digest || !until} onPress={() => register.mutate(undefined)}>
            Register
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <RiskBadge risk="R3" />
        </div>
        <TextField label="SPIFFE ID" value={spiffe} onChange={setSpiffe} mono isRequired className="sm:col-span-2" placeholder="spiffe://uk-1.prod.id.zoiko/ns/payments/sa/agent" />
        <TextField label="Agent principal ID" value={agent} onChange={setAgent} mono isRequired />
        <TextField label="Blueprint version" value={version} onChange={setVersion} mono isRequired />
        <TextField label="Approved artifact digest" value={digest} onChange={setDigest} mono isRequired placeholder="sha256:…" />
        <TextField label="Environment" value={env} onChange={setEnv} mono />
        <Select label="Attestation required" value={attest} onChange={setAttest} options={["0", "1", "2", "3"].map((a) => ({ id: a, label: `ATTEST-${a}` }))} />
        <TextField label="Valid until" type="date" value={until} onChange={setUntil} isRequired />
      </div>
      {register.error ? <ApiErrorView error={register.error} /> : null}
    </Dialog>
  );
}
