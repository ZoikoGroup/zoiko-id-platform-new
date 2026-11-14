"use client";

import type { AgentItem, ReadinessDryRun, ReadinessSetup } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, CopyId, PageHeader, Select, TextField } from "@zoiko/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { newIdempotencyKey, useCellMutation, useCellQuery } from "@/lib/cell";
import { useMe } from "../../authority/shared";
import { AGENTS_KEY } from "../shared";

import { AgentDryRunResults, AgentSetupStepper, EffectiveScopeSummary, ExecutionModeCard } from "./components";
import {
  PURPOSE_LABEL,
  READINESS_KEY,
  STAGES,
  TEMPLATE_NAME,
  canRequestPublish,
  fieldIssues,
  firstInvalid,
  furthestStage,
  publishGates,
  readinessMessage,
  stageIndex,
  type StageId,
} from "./logic";
import { PauseAgentControl } from "./pause-control";
import { ReadinessQuery, WriteError } from "./shared";

const KIND_OPTIONS = [{ id: "mandates", label: "Mandate metadata" }];

type Form = { agentId: string; name: string; kinds: string[]; maxRecords: string; windowDays: string };

function formFrom(setup: ReadinessSetup | undefined, agentId: string, agentName: string): Form {
  return {
    agentId,
    name: setup?.display_name ?? agentName,
    kinds: setup?.scope.resource_kinds ?? ["mandates"],
    maxRecords: String(setup?.scope.max_records ?? 100),
    windowDays: String(setup?.scope.warning_window_days ?? 14),
  };
}

/** WF-AG-01 and WF-AG-02: one wizard, five stages. Tenant and region are never typed; the server sets them and decides every result. */
export function SetupWizardPage({ agentId }: { agentId?: string }) {
  const setups = useCellQuery(READINESS_KEY, (api) => api.accessReadiness.setups());
  const agents = useCellQuery([...AGENTS_KEY, ""], (api) => api.agents.list(undefined));
  return (
    <>
      <PageHeader
        title={agentId ? "Set up access readiness" : "Create access readiness agent"}
        description="A reference agent that checks mandate metadata against fixed rules and proposes reviews for a person. It never changes access."
        breadcrumbs={[{ label: "Agents & tools", href: "/admin/agents" }, { label: "Access readiness", href: "/admin/agents/readiness" }, { label: agentId ? "Setup" : "Create agent" }]}
      />
      <ReadinessQuery query={setups}>
        {(data) => <Wizard initialAgentId={agentId} existing={data.items} agents={agents.data?.items ?? []} agentsLoaded={agents.isSuccess} />}
      </ReadinessQuery>
    </>
  );
}

function Wizard({ initialAgentId, existing, agents, agentsLoaded }: { initialAgentId?: string; existing: ReadinessSetup[]; agents: AgentItem[]; agentsLoaded: boolean }) {
  const me = useMe();
  const router = useRouter();
  const [agentId, setAgentId] = useState(initialAgentId ?? "");
  const found = existing.find((s) => s.agent_id === agentId);
  const [setup, setSetup] = useState<ReadinessSetup | undefined>(found);
  const agent = agents.find((a) => a.agent_principal_id === agentId);
  const [form, setForm] = useState<Form>(() => formFrom(found, initialAgentId ?? "", agents.find((a) => a.agent_principal_id === initialAgentId)?.agent_name ?? ""));
  const [stage, setStage] = useState<StageId>(found ? furthestStage(found) : "purpose");
  const [dirty, setDirty] = useState(false);
  const [issues, setIssues] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [dry, setDry] = useState<ReadinessDryRun | null>(null);
  const [pauseError, setPauseError] = useState<unknown>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const keys = [READINESS_KEY];

  const create = useCellMutation((api, v: Form) => api.accessReadiness.createSetup({ agent_principal_id: v.agentId, display_name: v.name, resource_kinds: v.kinds, max_records: Number(v.maxRecords), warning_window_days: Number(v.windowDays) }), { invalidate: keys });
  const change = useCellMutation(
    (api, v: Form) => api.accessReadiness.changeSetup(v.agentId, { display_name: v.name, resource_kinds: v.kinds, max_records: Number(v.maxRecords), warning_window_days: Number(v.windowDays), expected_revision: setup?.draft_revision }),
    { invalidate: keys },
  );
  const test = useCellMutation(async (api, id: string) => ({ dry: await api.accessReadiness.dryRun(id), setup: await api.accessReadiness.setup(id) }), { invalidate: keys });
  const requestPublish = useCellMutation((api, id: string) => api.accessReadiness.requestPublish(id), { invalidate: keys, success: "Publishing approval requested" });
  const approve = useCellMutation((api, id: string) => api.accessReadiness.approvePublish(id), { invalidate: keys, success: "Approved" });
  const pause = useCellMutation((api, v: { id: string; reason: string }) => api.accessReadiness.pause(v.id, v.reason), { invalidate: keys, success: "Future runs paused" });
  const resume = useCellMutation((api, id: string) => api.accessReadiness.resume(id), { invalidate: keys, success: "Future runs resumed" });
  const startRun = useCellMutation((api, id: string) => api.accessReadiness.startRun(id, newIdempotencyKey()), { invalidate: keys });
  const busy = create.isPending || change.isPending || test.isPending || requestPublish.isPending;

  const edit = (patch: Partial<Form>) => {
    setForm((f) => ({ ...f, ...patch }));
    setDirty(true);
    setIssues({});
  };

  function pickAgent(id: string) {
    const s = existing.find((x) => x.agent_id === id);
    const a = agents.find((x) => x.agent_principal_id === id);
    setAgentId(id);
    setSetup(s);
    setForm(formFrom(s, id, a?.agent_name ?? ""));
    setDirty(false);
    setIssues({});
    setFailure(null);
  }

  function reject(e: unknown) {
    const found = fieldIssues(e);
    setIssues(found);
    setFailure(e);
    const first = firstInvalid(found);
    // Focus goes to the first field the server refused; with no field to name, to the error list.
    setTimeout(() => {
      const el = first ? formRef.current?.querySelector<HTMLElement>(`[name="${first}"]`) : null;
      (el ?? summaryRef.current)?.focus();
    }, 0);
  }

  async function save(): Promise<ReadinessSetup | undefined> {
    setFailure(null);
    setIssues({});
    if (!agentId) {
      setIssues({ agent: "Choose the agent first." });
      setTimeout(() => summaryRef.current?.focus(), 0);
      return undefined;
    }
    if (!agent?.sponsor_principal_id && !setup) {
      setIssues({ sponsor: readinessMessage("AGENT_SPONSOR_REQUIRED", "") });
      setTimeout(() => summaryRef.current?.focus(), 0);
      return undefined;
    }
    try {
      if (!dirty && setup) return setup;
      const saved = setup ? await change.mutateAsync({ ...form, agentId }) : await create.mutateAsync({ ...form, agentId });
      setSetup(saved);
      setDirty(false);
      setDry(null);
      return saved;
    } catch (e) {
      reject(e);
      return undefined;
    }
  }

  async function next() {
    const i = stageIndex(stage);
    if (stage === "purpose" || stage === "scope") {
      if (!(await save())) return;
    }
    const target = STAGES[i + 1];
    if (target) setStage(target.id);
  }

  async function runTest() {
    if (!setup) return;
    setFailure(null);
    try {
      const r = await test.mutateAsync(setup.agent_id);
      setDry(r.dry);
      setSetup(r.setup);
    } catch (e) {
      reject(e);
    }
  }

  async function act<T>(fn: () => Promise<T>, then?: (r: T) => void) {
    setFailure(null);
    try {
      const r = await fn();
      then?.(r);
    } catch (e) {
      reject(e);
    }
  }

  const furthest: StageId = !setup ? "purpose" : stage === "publish" ? "publish" : furthestStage(setup) === "publish" ? "publish" : "test";
  const errorList = Object.entries(issues);
  const invalidStages: StageId[] = [
    ...(issues.agent || issues.sponsor || issues.display_name || issues.purpose ? (["purpose"] as StageId[]) : []),
    ...(issues.resource_kinds || issues.max_records || issues.warning_window_days ? (["scope"] as StageId[]) : []),
  ];
  const gates = publishGates(setup);
  const waiting = setup?.status === "PENDING_APPROVAL";
  const iRequested = setup?.approval?.requested_by === me;

  const rail = <EffectiveScopeSummary setup={setup ?? null} />;

  return (
    <div className="space-y-5">
      <AgentSetupStepper current={stage} furthest={furthest} dirty={dirty} invalid={invalidStages} onGo={setStage} />
      <div className="grid gap-5 min-[901px]:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <form
          ref={formRef}
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            void next();
          }}
          className="min-w-0 space-y-5"
        >
          <div ref={summaryRef} tabIndex={-1} aria-live="polite" className="outline-none">
            {errorList.length ? (
              <Banner tone="danger" title="Check these fields" live="assertive">
                <ul className="list-disc ps-5">
                  {errorList.map(([f, m]) => (
                    <li key={f}>{m}</li>
                  ))}
                </ul>
              </Banner>
            ) : null}
            {failure && !errorList.length ? <WriteError error={failure} /> : null}
          </div>

          {stage === "purpose" ? (
            <Card title="1. Purpose" description="What this agent is for, who answers for it, and its name.">
              <div className="space-y-4">
                <div>
                  <p className="text-sm font-medium text-ink">Agent type</p>
                  <p className="mt-1 flex flex-wrap items-center gap-2 text-sm">
                    <Badge tone="brand">{TEMPLATE_NAME}</Badge>
                    <span className="text-ink-muted">Locked template. Custom agents and tools cannot be added here.</span>
                  </p>
                </div>
                {initialAgentId || setup ? (
                  <p className="text-sm">
                    <span className="font-medium">Agent: </span>
                    {agent?.agent_name ?? setup?.display_name} <CopyId value={agentId} label="agent" />
                  </p>
                ) : (
                  <Select
                    label="Registered agent"
                    isRequired
                    placeholder={agentsLoaded ? "Choose an agent registered in this workspace" : "Loading agents…"}
                    options={agents.filter((a) => !a.parent_agent_id).map((a) => ({ id: a.agent_principal_id, label: a.agent_name, description: a.status }))}
                    value={agentId || null}
                    onChange={pickAgent}
                    errorMessage={issues.agent}
                    description="Register the agent first under Agents. This setup does not create identities."
                  />
                )}
                <TextField
                  label="Display name"
                  name="display_name"
                  isRequired
                  value={form.name}
                  onChange={(v) => edit({ name: v })}
                  errorMessage={issues.display_name}
                  placeholder="Access Readiness — Finance"
                  description="A label people recognize, up to 80 characters. Not unique; the identity is the immutable ID."
                />
                <div>
                  <p className="text-sm font-medium text-ink">
                    Sponsor <span aria-hidden className="text-danger-fg">*</span>
                  </p>
                  {agentId ? (
                    agent?.sponsor_principal_id || setup ? (
                      <p className="mt-1 text-sm">
                        {agent?.sponsor_principal_id ? <CopyId value={agent.sponsor_principal_id} label="sponsor" /> : null}{" "}
                        <span className="text-ink-muted">
                          {setup?.agent ? (setup.agent.sponsor_active ? "Active and accountable." : "Not active. The agent cannot be published.") : "Checked by the server when you continue."}
                        </span>
                      </p>
                    ) : (
                      <p className="mt-1 text-sm text-danger-fg">This agent has no sponsor. A sponsor is required. Assign one under Agents, then return.</p>
                    )
                  ) : (
                    <p className="mt-1 text-sm text-ink-muted">Shown after you choose the agent. It must be an active member of this workspace.</p>
                  )}
                </div>
                <div>
                  <p className="text-sm font-medium text-ink">Business purpose</p>
                  <p className="mt-1 text-sm">
                    {PURPOSE_LABEL} <span className="text-ink-muted">· the only purpose available</span>
                  </p>
                </div>
                <p className="text-sm text-ink-muted">Tenant and region are set from your signed-in workspace. They cannot be typed here.</p>
              </div>
            </Card>
          ) : null}

          {stage === "scope" ? (
            <Card title="2. Scope" description="What it may read. The effective controls beside this form show what policy will really allow.">
              <div className="space-y-4">
                <fieldset>
                  <legend className="text-sm font-medium text-ink">Required resources</legend>
                  <p className="text-xs text-ink-subtle">Chosen from a fixed list. Service-credential status is read for the agent itself and is always included.</p>
                  <div className="mt-2 space-y-1">
                    {KIND_OPTIONS.map((k) => (
                      <label key={k.id} className="flex min-h-11 items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          name="resource_kinds"
                          aria-invalid={issues.resource_kinds ? true : undefined}
                          aria-describedby={issues.resource_kinds ? "resource-kinds-error" : undefined}
                          checked={form.kinds.includes(k.id)}
                          onChange={(e) => edit({ kinds: e.target.checked ? [...new Set([...form.kinds, k.id])] : form.kinds.filter((x) => x !== k.id) })}
                          className="size-5"
                        />
                        {k.label}
                      </label>
                    ))}
                  </div>
                  {issues.resource_kinds ? <p id="resource-kinds-error" className="text-xs font-medium text-danger-fg">{issues.resource_kinds}</p> : null}
                </fieldset>
                <div className="grid gap-4 sm:grid-cols-2">
                  <TextField
                    label="Records per run"
                    name="max_records"
                    type="number"
                    inputMode="numeric"
                    isRequired
                    value={form.maxRecords}
                    onChange={(v) => edit({ maxRecords: v })}
                    errorMessage={issues.max_records}
                    description="1 to 500. The most mandate records one run may read."
                  />
                  <TextField
                    label="Expiry warning window (days)"
                    name="warning_window_days"
                    type="number"
                    inputMode="numeric"
                    isRequired
                    value={form.windowDays}
                    onChange={(v) => edit({ windowDays: v })}
                    errorMessage={issues.warning_window_days}
                    description="1 to 90. A mandate ending inside this window is flagged as expiring."
                  />
                </div>
              </div>
            </Card>
          ) : null}

          {stage === "runtime" ? (
            <div className="space-y-4">
              <ExecutionModeCard />
              <Banner tone="info" title="Tools are fixed">
                Only read mandate metadata, read agent identity status and create an access review proposal. Any other tool is refused at the gateway and the worker.
              </Banner>
            </div>
          ) : null}

          {stage === "test" ? (
            <Card title="4. Test" description="Run the rules on invented records. This is the only test; it never touches live data.">
              <div className="space-y-4">
                <Button type="button" variant="secondary" size="lg" pending={test.isPending} isDisabled={!setup} onPress={() => void runTest()}>
                  Run sandbox test
                </Button>
                {!setup ? <p className="text-sm text-ink-muted">Save the draft first.</p> : null}
                {dry ? <AgentDryRunResults result={dry} /> : setup?.validated ? <p className="text-sm text-ok-fg">A dry run already passed on this exact scope.</p> : null}
              </div>
            </Card>
          ) : null}

          {stage === "publish" && setup ? (
            <Card title="5. Publish" description="A different authorized person signs the exact digest below. Changing the scope makes it a new digest.">
              <div className="space-y-4 text-sm">
                <div>
                  <p className="font-medium">Digest the approver signs</p>
                  <code className="mt-1 block break-all rounded bg-sunken p-2 font-mono text-xs">{setup.scope_digest}</code>
                </div>
                <ul className="space-y-1" aria-label="Release gates">
                  {gates.map((g) => (
                    <li key={g.id}>
                      <span className="font-medium">{g.met ? "Met: " : "Not met: "}</span>
                      {g.label}
                      {g.met ? null : <span className="text-ink-muted"> — {g.why}</span>}
                    </li>
                  ))}
                </ul>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="lg"
                    isDisabled={!canRequestPublish(setup)}
                    pending={requestPublish.isPending}
                    onPress={() => void act(() => requestPublish.mutateAsync(setup.agent_id), setSetup)}
                  >
                    Request publishing approval
                  </Button>
                  {waiting && !iRequested ? (
                    <Button type="button" size="lg" variant="secondary" pending={approve.isPending} onPress={() => void act(() => approve.mutateAsync(setup.agent_id), setSetup)}>
                      Approve this digest
                    </Button>
                  ) : null}
                </div>
                {waiting ? (
                  <p className="text-ink-muted">
                    Waiting for approval{iRequested ? ". You requested it, so another person has to approve" : ""}
                    {setup.approval ? `. The request expires ${setup.approval.expires_at}` : ""}.
                  </p>
                ) : null}
                {setup.status === "APPROVED" || setup.status === "PAUSED" ? (
                  <div className="space-y-3 border-t border-border pt-4">
                    <Badge tone={setup.status === "APPROVED" ? "ok" : "warn"}>{setup.status === "APPROVED" ? "Approved" : "Paused"}</Badge>
                    {setup.paused_reason ? <p className="text-ink-muted">Paused: {setup.paused_reason}</p> : null}
                    <div className="flex flex-wrap gap-2">
                      {setup.status === "APPROVED" ? (
                        <Button
                          type="button"
                          size="lg"
                          pending={startRun.isPending}
                          onPress={() =>
                            void act(() => startRun.mutateAsync(setup.agent_id), (run) => {
                              router.push(`/admin/agents/readiness/${encodeURIComponent(setup.agent_id)}/runs/${encodeURIComponent(run.run_id)}`);
                            })
                          }
                        >
                          Start a run
                        </Button>
                      ) : null}
                      <PauseAgentControl
                        paused={setup.status === "PAUSED"}
                        pending={pause.isPending || resume.isPending}
                        error={pauseError ? <WriteError error={pauseError} /> : undefined}
                        onPause={(reason) => {
                          setPauseError(null);
                          void pause.mutateAsync({ id: setup.agent_id, reason }).then(setSetup, setPauseError);
                        }}
                        onResume={() => void act(() => resume.mutateAsync(setup.agent_id), setSetup)}
                      />
                    </div>
                    {pauseError ? <WriteError error={pauseError} /> : null}
                    <Link className="inline-flex min-h-11 items-center font-medium text-brand-700 underline" href="/admin/agents/readiness">
                      See runs and review proposals
                    </Link>
                  </div>
                ) : null}
              </div>
            </Card>
          ) : null}

          <div className="sticky bottom-0 flex flex-col gap-2 border-t border-border bg-canvas py-3 min-[601px]:flex-row min-[601px]:justify-between">
            <Button type="button" variant="secondary" size="lg" className="w-full min-[601px]:w-auto" isDisabled={stage === "purpose" || busy} onPress={() => setStage(STAGES[Math.max(0, stageIndex(stage) - 1)]?.id ?? "purpose")}>
              Back
            </Button>
            <div className="flex flex-col gap-2 min-[601px]:flex-row">
              {stage === "purpose" || stage === "scope" ? (
                <Button type="button" variant="secondary" size="lg" className="w-full min-[601px]:w-auto" pending={create.isPending || change.isPending} onPress={() => void save()}>
                  Save draft
                </Button>
              ) : null}
              {stage !== "publish" ? (
                <Button type="submit" size="lg" className="w-full min-[601px]:w-auto" pending={busy} isDisabled={stage === "test" && !setup?.validated}>
                  Validate and continue
                </Button>
              ) : null}
            </div>
          </div>
          <p className="text-xs text-ink-subtle">Save draft stores no credentials. Continuing checks your entries on the server.</p>
        </form>

        <div className="min-w-0">
          <div className="hidden min-[901px]:block">{rail}</div>
          <details className="min-[901px]:hidden">
            <summary className="flex min-h-11 cursor-pointer items-center font-medium text-brand-700">Permissions summary</summary>
            <div className="mt-2">{rail}</div>
          </details>
        </div>
      </div>
    </div>
  );
}
