"use client";

import type { WorkloadBindingView } from "@zoiko/api-client";
import { Banner, Button, Card, CopyId, DataTable, DateTime, Dialog, EmptyState, PageHeader, RiskBadge, Select, StatusBadge, TextField, humanize, type Column, type Option } from "@zoiko/ui";
import { Plus, ServerCog, ShieldAlert } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { NEEDS, slug, spiffeIdFor } from "../agents/agents-logic";
import { AuthorityNotice } from "../agents/pickers";
import { AGENTS_KEY, BLUEPRINTS_KEY } from "../agents/shared";

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
            <Select
              label="Environment"
              placeholder="Choose an environment…"
              value={env || null}
              onChange={setEnv}
              options={environmentOptions(bindings.data?.items ?? [])}
              description="The ones your bindings use, plus the usual four."
            />
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
      <RegisterDialog isOpen={registering} onClose={() => setRegistering(false)} trustDomain={trust.data?.trust_domain ?? null} />
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

/** The environments to offer: the ones bindings already use first, then the usual four, each once. */
function environmentOptions(items: WorkloadBindingView[]): Option[] {
  const used = [...new Set(items.map((b) => b.environment_id))];
  const out: Option[] = used.map((id) => ({ id, label: id, description: "In use by a binding" }));
  for (const e of ENVIRONMENTS) if (!used.includes(e.id)) out.push(e);
  return out;
}

const ENVIRONMENTS: Option[] = [
  { id: "prod", label: "Production", description: "prod" },
  { id: "staging", label: "Staging", description: "staging" },
  { id: "dev", label: "Development", description: "dev" },
  { id: "test", label: "Test", description: "test" },
];

const ATTESTATION: Option[] = [
  { id: "0", label: "ATTEST-0 · none", description: "No proof of what is running." },
  { id: "1", label: "ATTEST-1 · environment", description: "The runtime environment is the expected one." },
  { id: "2", label: "ATTEST-2 · artifact (recommended)", description: "The running image matches the approved digest. Needed to get a token." },
  { id: "3", label: "ATTEST-3 · hardware", description: "Hardware or TEE evidence. Not available yet." },
];

const VALID_FOR: Option[] = [
  { id: "30", label: "30 days" },
  { id: "90", label: "90 days" },
  { id: "365", label: "1 year" },
];

function RegisterDialog({ isOpen, onClose, trustDomain }: { isOpen: boolean; onClose: () => void; trustDomain: string | null }) {
  const agents = useCellQuery(AGENTS_KEY, (a) => a.agents.list(), { enabled: isOpen });
  const blueprints = useCellQuery(BLUEPRINTS_KEY, (a) => a.agents.blueprints(), { enabled: isOpen });
  const company = useCellQuery(["tenant", "me"], (a) => a.tenant.me(), { enabled: isOpen });
  const [agentId, setAgentId] = useState("");
  const [namespace, setNamespace] = useState("agents");
  const [account, setAccount] = useState("");
  const [byHand, setByHand] = useState("");
  const [env, setEnv] = useState("prod");
  const [attest, setAttest] = useState("2");
  const [days, setDays] = useState("90");

  const choices = (agents.data?.items ?? []).filter((a) => a.status !== "REVOKED" && a.status !== "RETIRED");
  const agent = choices.find((a) => a.agent_principal_id === agentId) ?? null;
  // The version and digest come from the agent's own blueprint release, so a binding cannot name one the agent is not built from.
  const release = agent ? (blueprints.data?.items ?? []).find((b) => b.blueprint_id === agent.blueprint_id && b.version === agent.blueprint_version) : undefined;
  const tenantId = company.data?.tenant_id ?? "";
  const suggested = agent && trustDomain && tenantId ? spiffeIdFor({ trustDomain, tenantId, namespace, account: account || agent.agent_name }) : "";
  const spiffe = byHand.trim() || suggested;

  const register = useCellMutation(
    (api, validTo: string) =>
      api.workloads.register({
        agent_principal_id: agentId,
        spiffe_id: spiffe,
        blueprint_version: agent?.blueprint_version ?? "",
        artifact_digest: release?.artifact_digest ?? "",
        environment_id: env,
        attestation_required: Number(attest),
        valid_to: validTo,
      }),
    { invalidate: [WL_KEY, AGENTS_KEY], success: "Workload binding registered", onSuccess: onClose },
  );
  const ready = Boolean(agent && release && spiffe.startsWith("spiffe://"));

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Register a workload binding"
      description="Lets one running workload act as one agent. An identity alone authorizes nothing."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={register.isPending} isDisabled={!ready} onPress={() => register.mutate(new Date(Date.now() + Number(days) * 86_400_000).toISOString())}>
            Register
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <RiskBadge risk="R3" />
        <AuthorityNotice needs={NEEDS.workload} what="bind a workload" />
        {!trustDomain ? <Banner tone="warn" title="No trust domain is configured for this cell, so an identity cannot be built here." /> : null}
        <Select
          label="Agent"
          isRequired
          placeholder={agents.isPending ? "Loading agents…" : choices.length ? "Choose the agent this workload runs…" : "No agents yet"}
          value={agentId || null}
          onChange={setAgentId}
          options={choices.map((a) => ({ id: a.agent_principal_id, label: a.agent_name, description: `${a.blueprint_id} @ ${a.blueprint_version} · ${humanize(a.status)}` }))}
          description={choices.length ? undefined : "Register an agent first (Agents & tools → Agents)."}
        />
        {agent ? (
          <div className="rounded-card border border-border bg-canvas p-3 text-sm">
            <p className="font-medium text-ink">Built from</p>
            <p className="mt-1 text-ink-muted">
              {agent.blueprint_id} @ {agent.blueprint_version}
              {release ? (
                <>
                  {" "}
                  · artifact <CopyId value={release.artifact_digest} label="artifact digest" />
                </>
              ) : (
                " · that release was not found"
              )}
            </p>
          </div>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Namespace" mono value={namespace} onChange={setNamespace} description="A group name for the workload, such as payments." />
          <TextField label="Service account" mono value={account} onChange={setAccount} placeholder={agent ? slug(agent.agent_name) : "agent"} description="Defaults to the agent's name." />
        </div>
        <div>
          <p className="text-sm font-medium text-ink">SPIFFE ID</p>
          <p className="mt-1 break-all rounded-card bg-sunken px-3 py-2 font-mono text-xs text-ink">{spiffe || "Choose an agent to build it."}</p>
          <p className="mt-1 text-xs text-ink-subtle">Built from this cell&apos;s trust domain, your company and the names above. The workload must run with exactly this identity.</p>
          <details className="mt-1">
            <summary className="cursor-pointer text-xs font-medium text-brand-700">Advanced: type the ID myself</summary>
            <div className="pt-2">
              <TextField label="SPIFFE ID" mono value={byHand} onChange={setByHand} placeholder="spiffe://…" description="Replaces the one above." />
            </div>
          </details>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <Select label="Environment" value={env} onChange={setEnv} options={ENVIRONMENTS} />
          <Select label="Proof required" value={attest} onChange={setAttest} options={ATTESTATION} />
          <Select label="Valid for" value={days} onChange={setDays} options={VALID_FOR} />
        </div>
        {register.error ? <ApiErrorView error={register.error} /> : null}
      </div>
    </Dialog>
  );
}
