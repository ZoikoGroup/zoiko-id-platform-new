"use client";

import type { KybPolicyVersion } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { Mono, RegError } from "./common";
import { PackBuilder } from "./kyb-pack-builder";
import { groupProblems, parsePack, problemText, rollbackProblem, rolesStillNeeded, shortHash, SIGN_OFF_ROLES, type SignOffRole } from "./kyb-policy-logic";

const KEY = ["regulatory", "kyb-policies"] as const;

function Detail({ hash }: { hash: string }) {
  const q = useCellQuery([...KEY, hash], (api) => api.regulatory.kybPolicy(hash));
  const [role, setRole] = useState<SignOffRole>("COMPLIANCE");
  const refresh = [KEY, [...KEY, hash]];
  const approve = useCellMutation((api, r: SignOffRole) => api.regulatory.kybPolicyApprove(hash, r), { invalidate: refresh as never, success: "Signed this exact version" });
  const publish = useCellMutation<void, unknown>((api) => api.regulatory.kybPolicyPublish(hash), { invalidate: refresh as never, success: "Put in force for its market" });
  return (
    <QueryView query={q}>
      {(d) => {
        const { signOffs, other } = groupProblems(d.problems);
        const needed = rolesStillNeeded(d.approved.map(([r]) => r));
        return (
          <Card title={`${d.market_key} · ${d.version}`} description={`Digest ${shortHash(d.policy_hash)}. You sign this exact digest: if the pack changes, it is a different version.`}>
            <div className="space-y-3 text-sm">
              <p>{d.in_force ? <Badge tone="ok">In force</Badge> : d.publishable ? <Badge tone="info">Ready to publish</Badge> : <Badge tone="warn">Not publishable yet</Badge>}</p>
              {d.high_impact_changes.length ? <p className="text-ink-muted">High-impact changes from the version in force: {d.high_impact_changes.join(", ")}. These need two different approvers.</p> : null}
              {other.length ? (
                <div>
                  <p className="font-medium text-ink">What is incomplete in the pack</p>
                  <ul className="list-disc space-y-1 pl-5">{other.map((c) => <li key={c}>{problemText(c)}</li>)}</ul>
                </div>
              ) : null}
              <div>
                <p className="font-medium text-ink">Sign-offs</p>
                <p className="text-ink-muted">Signed: {d.approved.length ? d.approved.map(([r]) => r.toLowerCase()).join(", ") : "none"}. Still needed: {needed.length ? needed.map((r) => r.toLowerCase()).join(", ") : "none"}.</p>
                {signOffs.length && !needed.length ? <ul className="list-disc pl-5">{signOffs.map((c) => <li key={c}>{problemText(c)}</li>)}</ul> : null}
              </div>
              <div className="flex flex-wrap items-end gap-2">
                <Select label="Sign as" value={role} onChange={(v) => setRole(v as SignOffRole)} options={SIGN_OFF_ROLES.map((r) => ({ id: r, label: r.charAt(0) + r.slice(1).toLowerCase() }))} />
                <Button variant="secondary" pending={approve.isPending} onPress={() => approve.mutate(role)}>Sign this version</Button>
                <Button pending={publish.isPending} isDisabled={!d.publishable || d.in_force} onPress={() => publish.mutate()}>Publish</Button>
              </div>
              {approve.error ? <RegError error={approve.error} /> : null}
              {publish.error ? <RegError error={publish.error} /> : null}
            </div>
          </Card>
        );
      }}
    </QueryView>
  );
}

export function KybPolicyPage() {
  const list = useCellQuery([...KEY], (api) => api.regulatory.kybPolicies());
  const [open, setOpen] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [rollback, setRollback] = useState<{ market: string; to: string; reason: string }>({ market: "", to: "", reason: "" });
  const parsed = text.trim() ? parsePack(text) : null;
  const versions = list.data?.items ?? [];
  const markets = [...new Set(versions.map((r) => r.market_key))];
  const propose = useCellMutation((api, pack: Record<string, unknown>) => api.regulatory.kybPolicyPropose(pack), { invalidate: [KEY as never], success: "Version stored", onSuccess: (r) => setOpen(r.policy_hash) });
  const back = useCellMutation((api, v: { market: string; to: string; reason: string }) => api.regulatory.kybPolicyRollback(v.market, v.to, v.reason), { invalidate: [KEY as never], success: "Rolled back" });
  const columns: Column<KybPolicyVersion>[] = [
    { key: "m", header: "Market", pinned: true, render: (r) => <Mono>{r.market_key}</Mono> },
    { key: "v", header: "Version", render: (r) => r.version },
    { key: "s", header: "State", render: (r) => (r.active ? <Badge tone="ok">In force</Badge> : <Badge tone="neutral">Stored</Badge>) },
    { key: "a", header: "Signed", render: (r) => (r.approved_roles.length ? r.approved_roles.map((x) => x.toLowerCase()).join(", ") : "none") },
    { key: "w", header: "Proposed", render: (r) => <DateTime value={r.created_at} /> },
    { key: "o", header: "", render: (r) => <Button variant="secondary" onPress={() => setOpen(r.policy_hash)}>Open</Button> },
  ];
  return (
    <>
      <PageHeader
        title="Company verification packs"
        description="What a market allows for company verification: sources and their lawful basis, documents and how old they may be, ownership thresholds, reviewers, retention. A pack is default off. It is signed in four roles by people other than its author and published by someone who signed nothing."
        breadcrumbs={[{ label: "Regulatory" }, { label: "Company verification packs" }]}
      />
      <div className="space-y-4">
        <QueryView query={list}>
          {(d) => <DataTable<KybPolicyVersion> caption="Versions, newest first" rows={d.items} rowKey={(r) => r.policy_hash} empty="No pack proposed yet. Every market is off." columns={columns} />}
        </QueryView>
        {open ? <Detail hash={open} /> : null}
        <PackBuilder onStored={setOpen} />
        <details className="rounded border p-3">
          <summary className="cursor-pointer font-medium">Advanced: paste a pack as JSON</summary>
          <div className="mt-3 space-y-3">
            <TextField label="Pack (JSON)" multiline rows={10} value={text} onChange={setText} />
            {parsed && !parsed.ok ? <p className="text-danger">{parsed.reason}</p> : null}
            <Button pending={propose.isPending} isDisabled={!parsed || !parsed.ok} onPress={() => parsed?.ok && propose.mutate(parsed.pack)}>Store this version</Button>
            {propose.error ? <RegError error={propose.error} /> : null}
            {propose.data && !propose.data.complete ? (
              <Banner tone="warn" title="Stored, and not complete">
                <ul className="list-disc pl-5">{propose.data.problems.map((c) => <li key={c}>{problemText(c)}</li>)}</ul>
              </Banner>
            ) : null}
          </div>
        </details>
        <Card title="Roll a market back" description="Puts an earlier version back in force as a new interval. What happened in between stays as it was.">
          <div className="space-y-3">
            <Select label="Market" value={rollback.market} onChange={(v) => setRollback({ ...rollback, market: v, to: "" })} options={[{ id: "", label: "Choose a market" }, ...markets.map((m) => ({ id: m, label: m }))]} />
            <Select label="Version to restore" value={rollback.to} onChange={(v) => setRollback({ ...rollback, to: v })} options={[{ id: "", label: rollback.market ? "Choose a version" : "Choose a market first" }, ...versions.filter((r) => r.market_key === rollback.market && !r.active).map((r) => ({ id: r.policy_hash, label: `${r.version} · ${shortHash(r.policy_hash)}` }))]} />
            <TextField label="Reason" value={rollback.reason} onChange={(v) => setRollback({ ...rollback, reason: v })} />
            {rollback.reason && rollbackProblem(rollback.reason) ? <p className="text-danger">{rollbackProblem(rollback.reason)}</p> : null}
            <Button variant="secondary" pending={back.isPending} isDisabled={rollbackProblem(rollback.reason) !== null || !rollback.market.trim() || !rollback.to.trim()} onPress={() => back.mutate({ market: rollback.market.trim(), to: rollback.to.trim(), reason: rollback.reason.trim() })}>
              Roll back
            </Button>
            {back.error ? <RegError error={back.error} /> : null}
          </div>
        </Card>
      </div>
    </>
  );
}
