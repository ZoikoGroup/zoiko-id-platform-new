"use client";

import type { WfCheckResult, WfSimulation, WfVersion, WfVersionSummary } from "@zoiko/api-client";
import { AccessUnavailable, Badge, Banner, Button, ButtonLink, Card, DataTable, EmptyState, JsonView, LoadingRows, PageHeader, Select } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { OUTCOMES, STARTER, STATUS, WF_KEY, canApprove, classify, parseDefinition, parseErrorText, problemText, requiredChecks } from "./logic";

function Problems({ items }: { items: { code: string; where: string }[] }) {
  if (!items.length) return null;
  return (
    <Banner tone="warn" title="This workflow does not pass the safety rules yet" live="polite">
      <ul className="list-disc ps-5">
        {items.map((p) => (
          <li key={`${p.code}-${p.where}`}>{problemText(p)}</li>
        ))}
      </ul>
    </Banner>
  );
}

function Failed({ error }: { error: unknown }) {
  const kind = classify(error);
  if (kind === "forbidden") return <EmptyState title="Withheld for your role">Your role does not include this. Nothing was changed.</EmptyState>;
  if (kind === "missing") return <AccessUnavailable correlationId={(error as { correlationId?: string }).correlationId} />;
  return <ApiErrorView error={error} />;
}

/** The workflows of this company. A workflow is data; nothing here runs one. */
export function WorkflowListPage() {
  const list = useCellQuery([...WF_KEY, "list"], (api) => api.workflows.list());
  return (
    <>
      <PageHeader
        title="Verification workflows"
        description="Which checks run and what happens on each result. A workflow never declines anyone: an approval needs every required check Verified, and every other result ends with a person."
        breadcrumbs={[{ label: "Workflows" }]}
        actions={<ButtonLink href="/admin/workflows/new">New workflow</ButtonLink>}
      />
      {list.isPending && list.fetchStatus !== "idle" ? <LoadingRows rows={4} /> : null}
      {list.isError ? <Failed error={list.error} /> : null}
      {list.data ? (
        <DataTable<WfVersionSummary>
          caption="Workflow versions"
          rows={list.data.items}
          rowKey={(r) => `${r.workflow_id}@${r.version}`}
          rowHref={(r) => `/admin/workflows/${encodeURIComponent(r.workflow_id)}/${encodeURIComponent(r.version)}`}
          empty="No workflows yet."
          columns={[
            { key: "id", header: "Workflow", pinned: true, render: (r) => <span className="font-medium">{r.workflow_id}</span> },
            { key: "version", header: "Version", render: (r) => r.version },
            { key: "status", header: "Status", render: (r) => <Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge> },
            { key: "hash", header: "Fingerprint", render: (r) => <code className="font-mono text-xs">{r.content_hash.slice(0, 10)}</code> },
          ]}
        />
      ) : null}
    </>
  );
}

/** The editor. A definition is checked without being saved, so a person sees what must change before anything exists. */
export function NewWorkflowPage() {
  const [text, setText] = useState(STARTER);
  const [local, setLocal] = useState<string | null>(null);
  const [result, setResult] = useState<WfCheckResult | null>(null);
  const check = useCellMutation((api, definition: Record<string, unknown>) => api.workflows.check(definition), { onSuccess: setResult });
  const save = useCellMutation((api, definition: Record<string, unknown>) => api.workflows.create(definition), { invalidate: [WF_KEY], success: "Draft saved" });

  function run(action: "check" | "save") {
    const parsed = parseDefinition(text);
    if (!parsed.ok) {
      setLocal(parsed.error);
      return;
    }
    setLocal(null);
    (action === "check" ? check : save).mutate(parsed.value);
  }

  const saved = save.data as WfVersion | undefined;
  return (
    <>
      <PageHeader
        title="New workflow"
        description="Write the definition as data. Check it as often as you like; saving makes a draft, and a draft cannot run."
        breadcrumbs={[{ label: "Workflows", href: "/admin/workflows" }, { label: "New" }]}
      />
      <div className="space-y-4">
        <label className="block text-sm font-medium text-ink" htmlFor="wf-definition">
          Definition
        </label>
        <textarea
          id="wf-definition"
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          rows={22}
          className="w-full rounded-control border border-border bg-surface p-3 font-mono text-xs text-ink focus-visible:ring-2 focus-visible:ring-brand-500"
        />
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" pending={check.isPending} onPress={() => run("check")}>
            Check
          </Button>
          <Button pending={save.isPending} onPress={() => run("save")}>
            Save draft
          </Button>
        </div>
        {local ? <Banner tone="danger" title="Not sent">{local}</Banner> : null}
        {result && !result.valid ? (
          <Banner tone="danger" title="The definition is not valid" live="polite">
            {parseErrorText(result.parse_error)} {result.detail}
          </Banner>
        ) : null}
        {result?.valid ? (
          result.problems.length ? <Problems items={result.problems} /> : <Banner tone="ok" title="Passes the safety rules">This version could be approved by a different person once saved.</Banner>
        ) : null}
        {check.error ? <Failed error={check.error} /> : null}
        {save.error ? <Failed error={save.error} /> : null}
        {saved ? (
          <Banner tone="ok" title="Draft saved">
            <ButtonLink href={`/admin/workflows/${encodeURIComponent(saved.workflow_id)}/${encodeURIComponent(saved.version)}`}>Open the draft</ButtonLink>
          </Banner>
        ) : null}
      </div>
    </>
  );
}

/** One version: its analysis, a simulation, and the maker-checker approval. */
export function WorkflowVersionPage({ id, version, me }: { id: string; version: string; me?: string }) {
  const key = [...WF_KEY, "version", id, version];
  const query = useCellQuery(key, (api) => api.workflows.version(id, version));
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [sim, setSim] = useState<WfSimulation | null>(null);
  const simulate = useCellMutation((api, results: Record<string, string>) => api.workflows.simulate(id, version, results), { onSuccess: setSim });
  const approve = useCellMutation((api) => api.workflows.approve(id, version), { invalidate: [WF_KEY], success: "Approved. This version is now frozen.", onSuccess: () => void query.refetch() });
  const retire = useCellMutation((api) => api.workflows.retire(id, version), { invalidate: [WF_KEY], success: "Retired", onSuccess: () => void query.refetch() });

  return (
    <>
      <PageHeader
        title={`${id} ${version}`}
        description="A workflow is data. Nothing on this page runs it."
        breadcrumbs={[{ label: "Workflows", href: "/admin/workflows" }, { label: `${id} ${version}` }]}
      />
      {query.isPending && query.fetchStatus !== "idle" ? <LoadingRows rows={4} /> : null}
      {query.isError ? <Failed error={query.error} /> : null}
      {query.data ? (
        <div className="space-y-4">
          <Banner tone={query.data.status === "APPROVED" ? "ok" : query.data.status === "RETIRED" ? "warn" : "info"} title={STATUS[query.data.status].label}>
            {STATUS[query.data.status].meaning}
          </Banner>
          <Problems items={query.data.problems ?? []} />

          <Card title="Simulate" description="Replay the workflow against results you choose. It stores and sends nothing. A check you do not choose counts as unavailable.">
            <div className="space-y-3">
              {requiredChecks(query.data.definition).map((check) => (
                <Select
                  key={check}
                  label={check.replaceAll("_", " ").toLowerCase()}
                  options={OUTCOMES.map((o) => ({ id: o, label: o.replaceAll("_", " ").toLowerCase() }))}
                  value={picked[check] ?? "VERIFIED"}
                  onChange={(v) => setPicked({ ...picked, [check]: v })}
                />
              ))}
              <Button
                variant="secondary"
                pending={simulate.isPending}
                onPress={() => simulate.mutate(Object.fromEntries(requiredChecks(query.data!.definition).map((c) => [c, picked[c] ?? "VERIFIED"])))}
              >
                Simulate
              </Button>
              {sim ? (
                <Banner tone={sim.outcome === "APPROVED" ? "ok" : "info"} title={`Ends: ${sim.outcome.replaceAll("_", " ").toLowerCase()}`} live="polite">
                  Steps: {sim.steps.join(" to ")}.{sim.needs_person.length ? ` A person reviews (${sim.needs_person.join(", ")}).` : ""} {sim.side_effects}
                </Banner>
              ) : null}
              {simulate.error ? <Failed error={simulate.error} /> : null}
            </div>
          </Card>

          <Card title="Approval" description="A different person approves a workflow, and only when it passes the safety rules. An approved version never changes.">
            <div className="flex flex-wrap items-center gap-3">
              {query.data.approved_by ? <span className="text-sm text-ink-muted">Approved by {query.data.approved_by.slice(0, 8)}…</span> : null}
              {query.data.status === "DRAFT" ? (
                <Button pending={approve.isPending} isDisabled={!canApprove(query.data.status, query.data.author, me, query.data.problems)} onPress={() => approve.mutate(undefined)}>
                  Approve this version
                </Button>
              ) : null}
              {query.data.status === "APPROVED" ? (
                <Button variant="secondary" pending={retire.isPending} onPress={() => retire.mutate(undefined)}>
                  Retire this version
                </Button>
              ) : null}
            </div>
            {query.data.status === "DRAFT" && me && me === query.data.author ? <p className="mt-2 text-sm text-ink-muted">You wrote this version, so someone else approves it.</p> : null}
            {approve.error ? <Failed error={approve.error} /> : null}
            {retire.error ? <Failed error={retire.error} /> : null}
          </Card>

          <Card title="Definition" description={`Fingerprint ${query.data.content_hash.slice(0, 16)}`}>
            <JsonView value={query.data.definition} label="Workflow definition" />
          </Card>
        </div>
      ) : null}
    </>
  );
}
