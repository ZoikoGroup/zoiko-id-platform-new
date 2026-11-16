"use client";

import type { GovLease, GovLeaseInput, GovPolicy, GovSimulation } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, Dialog, EmptyState, Select, TextField, type Column } from "@zoiko/ui";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { AgentGovernanceHeader } from "./shell";
import { GOV_KEY, codeText, govKey } from "./logic";

const toInput = (g: GovLease): GovLeaseInput => ({
  tool_id: g.tool_id,
  resource_kind: g.resource_kind,
  resource_selector: g.resource_selector,
  purpose: g.purpose,
  region: g.region,
  valid_until: g.valid_until,
});

/** Draft a new version: today's effective permissions plus one more (or the same list with a later expiry). Nothing here is live. */
function DraftDialog({ agentId, current, onClose }: { agentId: string; current: GovPolicy | null; onClose: () => void }) {
  const agent = useCellQuery(["admin", "agents", agentId], (a) => a.agents.get(agentId));
  const [tool, setTool] = useState("");
  const [kind, setKind] = useState("mandate");
  const [selector, setSelector] = useState("mandate:");
  const [purpose, setPurpose] = useState("access_posture_review");
  const [region, setRegion] = useState(current?.region ?? "");
  const [days, setDays] = useState("30");
  const [formError, setFormError] = useState<string | null>(null);
  const [created, setCreated] = useState<string | null>(null);

  const draft = useCellMutation<void, GovPolicy>(
    (api) => {
      const until = new Date(Date.now() + Number(days) * 86400000).toISOString();
      const keep = (current?.grants ?? []).map(toInput);
      return api.agentGovernance.draftPolicy(agentId, {
        leases: [...keep, { tool_id: tool, resource_kind: kind.trim(), resource_selector: selector.trim(), purpose: purpose.trim(), region: region.trim(), valid_until: until }],
        quotas: current?.quotas as Record<string, number | boolean> | undefined,
        region: region.trim(),
        ai_enabled: current?.ai_enabled ?? false,
        risk_tier: current?.risk_tier ?? "LOW",
      });
    },
    { invalidate: [GOV_KEY], onSuccess: (p) => setCreated(p.policy_id) },
  );
  const request = useCellMutation<string, { ref?: string }>((api, policy_id) => api.agentGovernance.requestChange(agentId, { policy_id, reason_code: "ACCESS_REVIEW" }), {
    invalidate: [GOV_KEY],
    success: "Approval requested. Nothing changes until it is signed and applied.",
    onSuccess: onClose,
  });

  function save() {
    setFormError(null);
    if (!tool) return setFormError("Choose the tool.");
    if (/[*?%]/.test(selector + kind + purpose + region) || !selector.trim() || !purpose.trim() || !region.trim()) return setFormError("Use exact values: no wildcards, nothing empty.");
    const n = Number(days);
    if (!Number.isInteger(n) || n < 1 || n > 90) return setFormError("Expiry is required: between 1 and 90 days.");
    draft.mutate();
  }

  const options = (agent.data?.allowed_tools ?? []).map((t) => ({ id: t, label: t }));
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && onClose()}
      size="lg"
      title="Request a permission change"
      description="Creates a draft version. It is requested access, not effective access, until independent people sign it and it is applied."
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>Cancel</Button>
          {created ? (
            <Button pending={request.isPending} onPress={() => request.mutate(created)}>Request approval</Button>
          ) : (
            <Button pending={draft.isPending} onPress={save}>Save draft</Button>
          )}
        </>
      }
    >
      <div className="space-y-4">
        {current?.grants.length ? <p className="text-sm text-ink-muted">The {current.grants.length} permission(s) in force now stay in the new version.</p> : null}
        <Select label="Tool" isRequired placeholder="Choose a tool this agent may use…" options={options} value={tool || null} onChange={setTool} />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Resource kind" isRequired value={kind} onChange={setKind} description="Exact. No wildcard." />
          <TextField label="Resource selector" isRequired value={selector} onChange={setSelector} description="An exact id, or a class ending in a colon such as mandate:" />
          <TextField label="Purpose" isRequired value={purpose} onChange={setPurpose} />
          <TextField label="Region" isRequired value={region} onChange={setRegion} description="The cell region it may be used in." />
          <TextField label="Expires in (days, 1 to 90)" isRequired value={days} onChange={setDays} inputMode="numeric" description="An expiry is always required." />
        </div>
        {formError ? <Banner tone="danger" title="Check the form" live="polite">{formError}</Banner> : null}
        {created ? <Banner tone="ok" title="Draft saved" live="polite">Now request approval to put it forward.</Banner> : null}
        {draft.error ? <ApiErrorView error={draft.error} /> : null}
        {request.error ? <ApiErrorView error={request.error} /> : null}
      </div>
    </Dialog>
  );
}

function Simulator({ agentId, region }: { agentId: string; region: string }) {
  const [tool, setTool] = useState("");
  const [kind, setKind] = useState("mandate");
  const [resource, setResource] = useState("mandate:synthetic-1");
  const [purpose, setPurpose] = useState("access_posture_review");
  const [action, setAction] = useState("");
  const sim = useCellMutation<void, GovSimulation>((api) =>
    api.agentGovernance.simulate(agentId, { tool_id: tool.trim(), resource_kind: kind.trim(), resource_id: resource.trim(), purpose: purpose.trim() || undefined, region: region || undefined, action: action.trim() || undefined }),
  );
  return (
    <Card title="Policy simulator" description="Synthetic requests only. Nothing is reserved, nothing is written except an evidence row. A simulation is not production enablement.">
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label="Tool" value={tool} onChange={setTool} />
          <TextField label="Resource kind" value={kind} onChange={setKind} />
          <TextField label="Synthetic resource id" value={resource} onChange={setResource} description="Must contain 'synthetic'." />
          <TextField label="Purpose" value={purpose} onChange={setPurpose} />
          <TextField label="Pretend action (optional)" value={action} onChange={setAction} description="For example role.grant, to see a platform prohibition." />
        </div>
        <Button isDisabled={!tool.trim()} pending={sim.isPending} onPress={() => sim.mutate()}>Simulate</Button>
        <div aria-live="polite">
          {sim.data ? (
            <Banner tone={sim.data.decision === "ALLOW" ? "ok" : "danger"} title={`Simulated: ${sim.data.decision === "ALLOW" ? "would be allowed" : `would be denied (${sim.data.code})`}`}>
              <p>{sim.data.decision === "DENY" ? codeText(sim.data.code) : "Every gate passed on the policy that is live now."}</p>
              <p className="mt-1 break-all font-mono text-2xs">Policy hash {sim.data.policy_hash ?? "none"}</p>
              <ol className="mt-2 list-decimal pl-5 text-xs">{sim.data.trace.map((t, i) => <li key={i}>{t.gate}: {t.result}</li>)}</ol>
              <p className="mt-2 text-xs">{sim.data.note}</p>
            </Banner>
          ) : null}
        </div>
        {sim.error ? <ApiErrorView error={sim.error} /> : null}
      </div>
    </Card>
  );
}

export function PermissionsWorkbench({ agentId }: { agentId: string }) {
  const access = useCellQuery(govKey("access", agentId), (a) => a.agentGovernance.effectiveAccess(agentId));
  const [drafting, setDrafting] = useState(false);

  const leaseColumns: Column<GovLease>[] = [
    { key: "tool", header: "Tool", pinned: true, render: (g) => <span className="font-mono text-xs">{g.tool_id}</span> },
    { key: "res", header: "Resource", render: (g) => `${g.resource_kind} ${g.resource_selector}` },
    { key: "purpose", header: "Purpose", render: (g) => g.purpose },
    { key: "region", header: "Region", render: (g) => g.region },
    { key: "exp", header: "Expires (UTC)", render: (g) => g.valid_until.slice(0, 10) },
    {
      key: "state",
      header: "State",
      render: (g) => <Badge size="xs" tone={g.state === "EFFECTIVE" ? "ok" : g.state === "EXPIRED" ? "danger" : "warn"}>{g.state === "REQUESTED" ? "Requested, not effective" : g.state === "EFFECTIVE" ? "Effective" : "Expired"}</Badge>,
    },
    { key: "src", header: "Ceiling", render: (g) => g.ceiling_source ?? "—", defaultHidden: true },
  ];

  return (
    <>
      <AgentGovernanceHeader
        agentId={agentId}
        view="permissions"
        title="Permissions"
        description="What this agent may do is the intersection of its approved leases and the company baseline. Requested changes and effective access are shown apart."
        actions={<Button onPress={() => setDrafting(true)}>Request a change</Button>}
      />
      <QueryView query={access}>
        {(a) => (
          <div className="space-y-4">
            {a.frozen ? <Banner tone="danger" title="Emergency stop in force" live="assertive">Every call is refused until the stop is restored by two independent people.</Banner> : null}
            {!a.company_baseline ? <Banner tone="warn" title="No company baseline">No agent can run until an Owner sets the company baseline (tools, regions, limits).</Banner> : null}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
              <div className="space-y-4 lg:col-span-3">
                <Card padded={false} title="Requested changes" description="Drafts and requests waiting for signatures. Not effective.">
                  <DataTable
                    caption="Requested policy versions"
                    rows={a.requested}
                    rowKey={(p) => p.policy_id}
                    empty={<EmptyState icon={ShieldCheck} title="No change is waiting">Use Request a change to draft one.</EmptyState>}
                    columns={[
                      { key: "v", header: "Version", render: (p) => `v${p.version}` },
                      { key: "s", header: "State", render: (p) => <Badge size="xs" tone="warn">{p.state === "DRAFT" ? "Draft" : "Waiting for approval"}</Badge> },
                      { key: "n", header: "Permissions", render: (p) => p.grants.length },
                      { key: "h", header: "Hash", render: (p) => <span className="font-mono text-2xs">{p.policy_hash.slice(0, 19)}…</span> },
                    ]}
                  />
                </Card>
                <Card padded={false} title="Permissions in force" description={a.policy ? `Version ${a.policy.version}. Server-computed; the page cannot change it.` : "No version is live."}>
                  <DataTable caption="Effective permissions" rows={a.effective_leases} rowKey={(g) => g.grant_id} columns={leaseColumns} empty={<EmptyState icon={ShieldCheck} title="No permission is in force">The agent can call nothing until a version is approved and applied.</EmptyState>} />
                </Card>
                <Simulator agentId={agentId} region={a.policy?.region ?? ""} />
              </div>
              <div className="space-y-4 lg:col-span-2">
                <Card title="Hard denies" description="Always shown, never togglable. No approval, by anyone, can lift these.">
                  <ul className="space-y-1">
                    {a.hard_denies.map((d) => (
                      <li key={d} className="flex items-center gap-2 text-sm"><Badge size="xs" tone="danger">Denied</Badge><span className="font-mono text-xs">{d}</span></li>
                    ))}
                  </ul>
                </Card>
                <Card title="Where the ceiling comes from" description="The first rule that says no wins; a missing source is a no.">
                  <ol className="list-decimal space-y-1 pl-5 text-sm">{a.hierarchy.map((h) => <li key={h}>{h}</li>)}</ol>
                  {a.company_baseline ? (
                    <p className="mt-3 text-xs text-ink-muted">
                      Company baseline allows {a.company_baseline.allowed_tools.length} tool(s) in {a.company_baseline.allowed_regions.join(", ") || "no region"}.
                    </p>
                  ) : null}
                </Card>
                {a.approval ? (
                  <Card title="Approval bound to the live version">
                    <p className="text-sm">{a.approval.ref ?? a.approval.request_id.slice(0, 8)}: {a.approval.signatures.length} signature(s)</p>
                    <p className="mt-1 break-all font-mono text-2xs">{a.approval.policy_hash}</p>
                  </Card>
                ) : null}
              </div>
            </div>
            {drafting ? <DraftDialog agentId={agentId} current={a.policy} onClose={() => setDrafting(false)} /> : null}
          </div>
        )}
      </QueryView>
    </>
  );
}
