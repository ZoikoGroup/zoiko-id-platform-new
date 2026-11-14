"use client";

import type { DaaraDryRun, DaaraRun } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, EmptyState, KeyValues, Select, Switch, TextField } from "@zoiko/ui";
import { PlayCircle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { DAARA_KEY, STAGE_LABEL, STEPS, blockerText, daaraKey, newKey, runHeadline, runLabel, stepEnabled, unlockedStep, type StepId } from "./daara-logic";
import { GOV_KEY, govKey } from "./logic";
import { AgentGovernanceHeader } from "./shell";

const ALL = [DAARA_KEY, GOV_KEY];

/** The five wizard steps (WF-AG-01/02). A step opens only when the gate before it has passed, and the server decides every gate. */
export function DaaraSetupPage({ agentId }: { agentId: string }) {
  const setup = useCellQuery(daaraKey("setup", agentId), (a) => a.daara.setup(agentId));
  const [picked, setPicked] = useState<StepId | null>(null);
  const [label, setLabel] = useState("");
  const [rationale, setRationale] = useState("");
  const [region, setRegion] = useState("");
  const [warn, setWarn] = useState("14");
  const [ai, setAi] = useState(false);
  const [schedule, setSchedule] = useState<"MANUAL" | "DAILY" | "">("");
  const [formError, setFormError] = useState<string | null>(null);
  const [dry, setDry] = useState<DaaraDryRun | null>(null);

  const data = setup.data;
  const step: StepId = picked ?? unlockedStep(data);
  const stage = data ? STAGE_LABEL[data.stage] : null;
  // the form shows what is saved until the person types: a saved setup is never silently replaced by an empty form
  const f = {
    label: label || data?.setup?.label || "",
    rationale: rationale || data?.setup?.rationale || "",
    region: region || data?.setup?.region || "",
    warn: warn !== "14" ? warn : String(data?.setup?.warn_days ?? 14),
    ai: ai || Boolean(data?.setup?.ai_explain),
    schedule: (schedule || data?.setup?.schedule || "MANUAL") as "MANUAL" | "DAILY",
  };

  const refresh = { invalidate: [...ALL] };
  const save = useCellMutation<void, unknown>(
    (api) => api.daara.saveSetup(agentId, { label: f.label.trim(), rationale: f.rationale.trim() || null, warn_days: Number(f.warn), region: f.region.trim(), ai_explain: f.ai, schedule: f.schedule }),
    { ...refresh, success: "Saved as a draft. Nothing is live." },
  );
  const validate = useCellMutation<void, unknown>((api) => api.daara.validate(agentId), { ...refresh, success: "The setup compiles." });
  const test = useCellMutation<void, DaaraDryRun>((api) => api.daara.dryRun(agentId), { ...refresh, onSuccess: (r) => setDry(r) });
  const publish = useCellMutation<void, { ref?: string }>((api) => api.daara.requestPublish(agentId), { ...refresh, success: "Approval requested. Nothing changes until it is signed and applied." });

  function saveStep() {
    setFormError(null);
    if (f.label.trim().length < 1 || f.label.trim().length > 80) return setFormError("Give the agent a descriptive name of up to 80 characters.");
    const n = Number(f.warn);
    if (!Number.isInteger(n) || n < 1 || n > 90) return setFormError("The warning window is 1 to 90 days.");
    if (!f.region.trim() || /[*?%]/.test(f.region)) return setFormError("Enter the exact region this agent runs in.");
    save.mutate();
  }

  const gates = [
    { id: "setup", ok: Boolean(data?.enabled), text: "Setup saved" },
    { id: "blockers", ok: Boolean(data?.enabled) && data?.blockers.length === 0, text: "No blocking requirement" },
    { id: "validated", ok: Boolean(data?.setup?.validated), text: "Validated by the policy compiler" },
    { id: "sandbox", ok: Boolean(data?.setup?.sandbox_passed), text: "Sandbox test passed for this exact setup" },
  ];
  const ready = gates.every((g) => g.ok);

  return (
    <>
      <AgentGovernanceHeader
        agentId={agentId}
        view="daara"
        title="Access readiness agent"
        description="A deterministic reference agent that checks mandates and agent identities and proposes a human review. It never changes access."
      />
      <QueryView query={setup}>
        {(d) => (
          <div className="space-y-4">
            <Banner tone="info" title="Off until published">
              {d.template.release_default}. Documentation and screens do not show that a service is deployed; the stage below comes from the server.
            </Banner>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
              <nav aria-label="Setup steps" className="lg:col-span-1">
                <ol className="space-y-1">
                  {STEPS.map((s, i) => {
                    const open = stepEnabled(d, s.id);
                    return (
                      <li key={s.id}>
                        <button
                          type="button"
                          disabled={!open}
                          aria-current={step === s.id ? "step" : undefined}
                          onClick={() => setPicked(s.id)}
                          className={`flex min-h-11 w-full items-center gap-3 rounded-card border px-3 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-50 ${
                            step === s.id ? "border-brand-500 bg-brand-50/40" : "border-border"
                          }`}
                        >
                          <span aria-hidden className="flex size-6 shrink-0 items-center justify-center rounded-full bg-sunken text-xs font-semibold">{i + 1}</span>
                          <span className="font-medium">{s.label}</span>
                          {!open ? <span className="sr-only"> (locked until the earlier step passes)</span> : null}
                        </button>
                      </li>
                    );
                  })}
                </ol>
                {stage ? (
                  <p className="mt-3 text-sm">
                    <Badge tone={stage.tone}>{stage.label}</Badge>
                    <span className="mt-1 block text-xs text-ink-muted">{stage.hint}</span>
                  </p>
                ) : null}
              </nav>

              <div className="space-y-4 lg:col-span-2">
                {step === "purpose" ? (
                  <Card title="1. Purpose" description="The template is locked: no custom agent and no tool upload in this release.">
                    <div className="space-y-4">
                      <TextField label="Display name" isRequired value={f.label} onChange={setLabel} description="A label only. The agent's identity is generated and never changes." />
                      <TextField label="Why (optional)" value={f.rationale} onChange={setRationale} multiline rows={3} description="Up to 500 characters. Do not include personal data." />
                      <KeyValues columns={1} items={[["Business purpose", "Access posture review (fixed)"], ["Template", d.template.template]]} />
                      <div className="grid gap-3 sm:grid-cols-2">
                        <div>
                          <p className="text-sm font-semibold">What it does</p>
                          <ul className="mt-1 list-disc pl-5 text-sm text-ink-muted">{d.template.does.map((x) => <li key={x}>{x}</li>)}</ul>
                        </div>
                        <div>
                          <p className="text-sm font-semibold">What it never does</p>
                          <ul className="mt-1 list-disc pl-5 text-sm text-ink-muted">{d.template.does_not.map((x) => <li key={x}>{x}</li>)}</ul>
                        </div>
                      </div>
                    </div>
                  </Card>
                ) : null}

                {step === "scope" ? (
                  <Card title="2. Scope" description="Requested access, shown apart from effective access. The server checks it.">
                    <div className="space-y-4">
                      <div className="grid gap-4 sm:grid-cols-2">
                        <TextField label="Region" isRequired value={f.region} onChange={setRegion} description="Pinned from the approved cell. Not editable to an arbitrary country." />
                        <TextField label="Warn before a mandate ends (days)" isRequired value={f.warn} onChange={setWarn} inputMode="numeric" description="1 to 90. A warning is not a revocation." />
                      </div>
                      <ul className="space-y-2">
                        {d.template.tools.map((t) => (
                          <li key={t.tool_id} className="rounded-card border border-border p-3 text-sm">
                            <p className="font-mono text-xs">{t.tool_id}</p>
                            <p className="text-ink-muted">{t.why}</p>
                            <p className="mt-1 text-xs text-ink-subtle">Action {t.action}, side effect {t.side_effect.toLowerCase()}. Any other tool is denied.</p>
                          </li>
                        ))}
                      </ul>
                      {d.effective_scope ? (
                        <p className="text-xs text-ink-muted">
                          Blocked for every agent, always: {d.effective_scope.blocked.join(", ")}. {d.effective_scope.note}
                        </p>
                      ) : null}
                      {d.blockers.length ? (
                        <Banner tone="warn" title="Not ready to validate" live="polite">
                          <ul className="list-disc pl-5">{d.blockers.map((b) => <li key={b}>{blockerText(b)}</li>)}</ul>
                        </Banner>
                      ) : null}
                    </div>
                  </Card>
                ) : null}

                {step === "runtime" ? (
                  <Card title="3. Runtime" description="Bounded, first-party, manual. Schedules need a company policy that does not exist yet.">
                    <div className="space-y-4">
                      <KeyValues columns={1} items={[["Worker", "Managed reference worker (fixed)"], ["Connector", "Approved internal metadata adapter (fixed)"]]} />
                      <Select
                        label="Execution"
                        options={[{ id: "MANUAL", label: "Manual runs only" }, { id: "DAILY", label: "Once a day (needs the company schedule policy)" }]}
                        value={f.schedule}
                        onChange={(v) => setSchedule(v as "MANUAL" | "DAILY")}
                        description="A schedule is refused unless the agent's approved policy, the company baseline and the platform all allow it. Today the platform does not."
                      />
                      <Switch isSelected={f.ai} onChange={setAi}>
                        Plain-language explanation by an approved model (off by default)
                      </Switch>
                      <p className="text-xs text-ink-muted">
                        The result never depends on it. It needs a provider the company approved by name, the policy to allow it, and a spending cap. Without them, or if the model
                        fails or says anything the findings do not support, the fixed text is used.
                      </p>
                    </div>
                  </Card>
                ) : null}

                {step === "test" ? (
                  <Card title="4. Test" description="The real rules on synthetic records. It reads nothing live and writes nothing.">
                    <div className="space-y-3">
                      <Button isDisabled={!d.setup?.validated} pending={test.isPending} onPress={() => test.mutate()} icon={<PlayCircle aria-hidden className="size-4" />}>
                        Run sandbox test
                      </Button>
                      {!d.setup?.validated ? <p className="text-xs text-ink-muted">Validate the setup first (step 2).</p> : null}
                      <div aria-live="polite">
                        {dry ? (
                          <Banner tone={dry.passed ? "ok" : "danger"} title={dry.passed ? "Sandbox passed on synthetic records" : "Sandbox did not pass"}>
                            <p>{dry.findings.length} of {dry.expected} expected findings reproduced. Records written: {dry.wrote}. This is a simulation, not production enablement.</p>
                            <ul className="mt-1 list-disc pl-5 text-xs">{dry.findings.map((x) => <li key={x}>{x}</li>)}</ul>
                          </Banner>
                        ) : null}
                      </div>
                      {test.error ? <ApiErrorView error={test.error} /> : null}
                    </div>
                  </Card>
                ) : null}

                {step === "publish" ? (
                  <Card title="5. Publish" description="A separate request that Security and Authority sign. Changing the setup afterwards voids it.">
                    <div className="space-y-3">
                      <ul className="space-y-1 text-sm">
                        {gates.map((g) => (
                          <li key={g.id} className="flex items-center gap-2">
                            <Badge size="xs" tone={g.ok ? "ok" : "warn"}>{g.ok ? "Done" : "Not yet"}</Badge>
                            {g.text}
                          </li>
                        ))}
                      </ul>
                      <Button isDisabled={!ready} pending={publish.isPending} onPress={() => publish.mutate()}>
                        Request publishing approval
                      </Button>
                      {!ready ? <p className="text-xs text-ink-muted">Disabled until every check above has passed.</p> : null}
                      {d.stage === "PENDING_APPROVAL" ? (
                        <Banner tone="info" title="Waiting for signatures">
                          Decide it under <Link className="underline" href="/admin/agents/governance/approvals">Approvals</Link>. Approved is not live: it goes live when applied.
                        </Banner>
                      ) : null}
                      {publish.error ? <ApiErrorView error={publish.error} /> : null}
                    </div>
                  </Card>
                ) : null}

                {step === "purpose" || step === "scope" || step === "runtime" ? (
                  <div className="flex flex-wrap gap-2">
                    <Button pending={save.isPending} onPress={saveStep}>Save draft</Button>
                    {step === "scope" ? (
                      <Button variant="secondary" isDisabled={!d.enabled} pending={validate.isPending} onPress={() => validate.mutate()}>
                        Validate
                      </Button>
                    ) : null}
                    {d.setup && !d.setup.validated && d.enabled && step !== "scope" ? <p className="self-center text-xs text-ink-muted">Changing the setup voids a passed validation and sandbox test.</p> : null}
                  </div>
                ) : null}
                {formError ? <Banner tone="danger" title="Check the form" live="polite">{formError}</Banner> : null}
                {save.error ? <ApiErrorView error={save.error} /> : null}
                {validate.error ? <ApiErrorView error={validate.error} /> : null}
              </div>

              <aside className="space-y-4 lg:col-span-1" aria-label="Effective controls">
                <Card title="Effective controls" description="Server-computed.">
                  <KeyValues
                    columns={1}
                    items={[
                      ["Sponsor", "Shown above, from the registry"],
                      ["Region", d.setup?.region ?? "Not set"],
                      ["Setup hash", d.setup ? <span key="h" className="break-all font-mono text-2xs">{d.setup.config_hash}</span> : "None"],
                      ["Live policy", d.policy ? `Version ${d.policy.version}` : "None"],
                    ]}
                  />
                </Card>
              </aside>
            </div>
            {d.stage === "ACTIVE" ? <RunsPanel agentId={agentId} /> : null}
          </div>
        )}
      </QueryView>
    </>
  );
}

function RunsPanel({ agentId }: { agentId: string }) {
  const runs = useCellQuery(daaraKey("runs", agentId), (a) => a.daara.runs(agentId), { refetchInterval: 30000 });
  const start = useCellMutation<boolean, DaaraRun>((api, queued) => api.daara.startRun(agentId, newKey(), queued ? "queued" : "inline"), { invalidate: [DAARA_KEY, GOV_KEY, govKey("overview")] });
  return (
    <Card
      padded={false}
      title="Runs"
      description="Manual only. A run reads minimal metadata, applies the rules and adds proposals to the review queue."
      actions={
        <span className="flex flex-wrap gap-2">
          <Button pending={start.isPending} onPress={() => start.mutate(false)}>Start a run</Button>
          <Button variant="secondary" pending={start.isPending} onPress={() => start.mutate(true)}>Queue a run</Button>
        </span>
      }
    >
      <div aria-live="polite" className="px-4 pt-3">
        {start.data ? (
          <Banner tone={start.data.state === "COMPLETED" ? "ok" : "warn"} title={`Run finished: ${runLabel(start.data).label}`}>
            {runHeadline(start.data)}
          </Banner>
        ) : null}
        {start.error ? <ApiErrorView error={start.error} /> : null}
      </div>
      <QueryView query={runs}>
        {(r) => (
          <DataTable
            caption="DAARA runs"
            rows={r.items}
            rowKey={(x) => x.run_id}
            empty={<EmptyState icon={PlayCircle} title="No run yet">Start a run when the policy is live.</EmptyState>}
            columns={[
              { key: "at", header: "Finished (UTC)", render: (x) => <span className="tabular text-xs">{(x.completed_at ?? "").replace("T", " ").slice(0, 19)}</span> },
              { key: "state", header: "Outcome", render: (x) => { const l = runLabel(x); return <Badge size="xs" tone={l.tone}>{l.label}</Badge>; } },
              { key: "sum", header: "Result", render: (x) => runHeadline(x) },
              { key: "open", header: "", render: (x) => <Link className="font-medium text-brand-700 underline" href={`/admin/agents/governance/${agentId}/runs/${x.run_id}`}>Open run</Link> },
            ]}
          />
        )}
      </QueryView>
    </Card>
  );
}
