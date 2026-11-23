"use client";

import type { DeploymentMode, GateReport, OnboardingPlan, TenantView } from "@zoiko/api-client";
import { Banner, Button, ButtonLink, Card, Checkbox, KeyValues, Meter, PageHeader, RadioCards, Select, StatusBadge, TextField } from "@zoiko/ui";
import { CheckCircle2, Circle, CircleDot, Lock, Rocket } from "lucide-react";
import { useState, type ReactNode } from "react";

import { ApprovalsWaiting } from "@/features/approvals/guided-approval";
import { APPS_KEY } from "@/features/onboarding/applications-page";
import { ConnectGuide, PilotReadiness, ShadowProgress } from "@/features/onboarding/connect-guide";
import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import {
  ApprovalStep,
  DOMAINS_KEY,
  GATES_KEY,
  GateList,
  LIFECYCLE,
  PLANS_KEY,
  TENANT_KEY,
  blockingCount,
  lifecycleIndex,
  standing,
  useTenant,
} from "./shared";
import { UserText } from "@/lib/user-text";

const MANAGE = "tenant.onboarding.manage";
const ACTIVATE = "tenant.onboarding.activate";

const MODES: { id: DeploymentMode; label: string; description: string }[] = [
  { id: "O0", label: "Native Zoiko iD", description: "Zoiko iD signs people in and decides access." },
  { id: "O1", label: "Authorization overlay", description: "Your identity provider signs people in; Zoiko iD decides protected actions." },
  { id: "O2", label: "Assurance overlay", description: "As O1, plus step-up, verification, authority and mandates." },
  { id: "O3", label: "Federated trust overlay", description: "Several organizations and identity providers share one authority plane." },
];

type StepState = "done" | "current" | "todo" | "optional";

function StepIcon({ state }: { state: StepState }) {
  if (state === "done") return <CheckCircle2 aria-hidden className="size-5 text-ok-fg" />;
  if (state === "current") return <CircleDot aria-hidden className="size-5 text-brand-700" />;
  return <Circle aria-hidden className="size-5 text-ink-subtle" />;
}

function Step({ n, title, state, summary, children }: { n: number; title: string; state: StepState; summary: ReactNode; children?: ReactNode }) {
  const [open, setOpen] = useState(state === "current");
  return (
    <li className="rounded-card border border-border bg-surface">
      <button type="button" className="flex w-full items-start gap-3 px-4 py-3 text-left" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <StepIcon state={state} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-ink">
              {n}. {title}
            </span>
            {state === "optional" ? <span className="text-xs text-ink-muted">optional</span> : null}
            {state === "done" ? <span className="text-xs text-ok-fg">done</span> : null}
          </div>
          <div className="mt-0.5 text-xs text-ink-muted">{summary}</div>
        </div>
      </button>
      {open && children ? <div className="border-t border-border px-4 py-4">{children}</div> : null}
    </li>
  );
}

function CreatePlan() {
  const [mode, setMode] = useState<DeploymentMode>("O0");
  const create = useCellMutation((api) => api.onboarding.createPlan({ target_mode: mode }), { invalidate: [PLANS_KEY], success: "Onboarding plan created" });
  return (
    <div className="space-y-4">
      <RadioCards label="How will your company use Zoiko iD?" value={mode} onChange={(v) => setMode(v as DeploymentMode)} columns={2} options={MODES} />
      <p className="text-xs text-ink-muted">
        The plan records the target mode and the shadow exit criteria (platform defaults: 100 shadow decisions, full high-risk context, no critical
        false allows, p95 within 60 ms). Criteria can only be made stricter.
      </p>
      <Button pending={create.isPending} onPress={() => create.mutate(undefined)}>
        Create plan
      </Button>
      {create.error ? <ApiErrorView error={create.error} title="The plan was not created" /> : null}
    </div>
  );
}

function PlanSummary({ plan }: { plan: OnboardingPlan }) {
  return (
    <KeyValues
      columns={3}
      items={[
        ["Plan", `v${plan.version}`],
        ["Status", <StatusBadge key="s" state={plan.status} kind="Plan" size="xs" />],
        ["Target mode", MODES.find((m) => m.id === plan.target_mode)?.label ?? plan.target_mode],
      ]}
    />
  );
}

const ROLLBACK_REASONS = [
  { id: "CRITICAL_FALSE_ALLOW", label: "Critical false allow" },
  { id: "ERROR_RATE", label: "Error or latency regression" },
  { id: "INCIDENT", label: "Open incident" },
  { id: "OWNER_DECISION", label: "Owner decision" },
];

/** Back to the previous safe stage (OVERLAY-001 §20): pilot scopes stop enforcing at once; nothing
 * about identities or authority is undone. Available from shadow and pilot. */
function Rollback({ plan }: { plan: OnboardingPlan }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("OWNER_DECISION");
  const roll = useCellMutation((api) => api.onboarding.rollback(plan.plan_id, reason), {
    invalidate: [TENANT_KEY, GATES_KEY, PLANS_KEY, ["onboarding", "stages", plan.plan_id]],
    success: (r) => `Rolled back to ${r.tenant_status.toLowerCase()}`,
    onSuccess: () => setOpen(false),
  });
  return (
    <Card title="Roll back" description="Return to the previous safe stage. Enforcement in pilot scopes stops immediately; nothing else is undone.">
      {open ? (
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Why" value={reason} onChange={setReason} options={ROLLBACK_REASONS} className="min-w-56" />
          <Button variant="danger" pending={roll.isPending} onPress={() => roll.mutate(undefined)}>
            Roll back now
          </Button>
          <Button variant="ghost" onPress={() => setOpen(false)}>
            Cancel
          </Button>
        </div>
      ) : (
        <Button variant="danger-outline" onPress={() => setOpen(true)}>
          Roll back…
        </Button>
      )}
      {roll.error ? <div className="mt-3"><ApiErrorView error={roll.error} title="Not rolled back" /></div> : null}
    </Card>
  );
}

const RISKS = [
  { id: "0", label: "R0 — read only" },
  { id: "1", label: "R1 — low risk" },
  { id: "2", label: "R2 — sensitive" },
  { id: "3", label: "R3 — critical" },
];

/** Pilot stages (OVERLAY-001 §19): only what an ACTIVE stage covers is enforced; everything else
 * stays evaluate-only. Each stage is activated by a second administrator. */
function PilotStages({ plan }: { plan: OnboardingPlan }) {
  const key = ["onboarding", "stages", plan.plan_id];
  const stages = useCellQuery(key, (api) => api.onboarding.stages(plan.plan_id));
  const apps = useCellQuery(APPS_KEY, (api) => api.integration.applications());
  const [name, setName] = useState("");
  const [risk, setRisk] = useState("1");
  const [chosen, setChosen] = useState<string[]>([]);
  const [activating, setActivating] = useState<string | null>(null);
  const add = useCellMutation(
    (api) => api.onboarding.addStage(plan.plan_id, name.trim(), { applications: chosen, max_risk: Number(risk) }),
    {
      invalidate: [key],
      success: "Stage planned",
      onSuccess: () => {
        setName("");
        setChosen([]);
      },
    },
  );
  const activate = useCellMutation((api, v: { stage: string; approval: string }) => api.onboarding.activateStage(v.stage, v.approval), {
    invalidate: [key],
    success: "Stage enforced",
    onSuccess: () => setActivating(null),
  });
  const active = (apps.data?.items ?? []).filter((a) => a.status === "ACTIVE");
  return (
    <div className="space-y-4">
      <QueryView query={stages}>
        {(r) =>
          r.items.length ? (
            <ul className="divide-y divide-border rounded-card border border-border">
              {r.items.map((st) => (
                <li key={st.stage_id} className="space-y-3 px-4 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-ink">{st.name}</span>
                      <StatusBadge state={st.status} kind="Stage" size="xs" />
                    </div>
                    <span className="text-xs text-ink-muted">
                      up to R{st.scope.max_risk} ·{" "}
                      {st.scope.applications.length ? `${st.scope.applications.length} application(s)` : "every application"}
                    </span>
                    {st.status === "PLANNED" ? (
                      <Button size="sm" variant="secondary" onPress={() => setActivating(activating === st.stage_id ? null : st.stage_id)}>
                        Enforce…
                      </Button>
                    ) : null}
                  </div>
                  {activating === st.stage_id ? (
                    <ApprovalStep
                      action={ACTIVATE}
                      resource={`onboarding_stage:${st.stage_id}`}
                      change="ACTIVATE"
                      label="Enforce stage"
                      onRun={(id) => activate.mutate({ stage: st.stage_id, approval: id })}
                      pending={activate.isPending}
                      error={activate.error}
                    />
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-muted">No stages yet. Start small: one application, low-risk actions.</p>
          )
        }
      </QueryView>
      <div className="space-y-3 rounded-card border border-border p-4">
        <div className="text-sm font-semibold text-ink">Plan a stage</div>
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label="Name" value={name} onChange={setName} placeholder="Finance team, low risk" />
          <Select label="Highest risk enforced" value={risk} onChange={setRisk} options={RISKS} />
        </div>
        {active.length ? (
          <fieldset className="space-y-2">
            <legend className="text-xs font-medium text-ink-muted">Applications (none selected = all)</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {active.map((a) => (
                <Checkbox
                  key={a.application_id}
                  isSelected={chosen.includes(a.application_id)}
                  onChange={() => setChosen((l) => (l.includes(a.application_id) ? l.filter((x) => x !== a.application_id) : [...l, a.application_id]))}
                >
                  {a.name}
                </Checkbox>
              ))}
            </div>
          </fieldset>
        ) : null}
        <Button size="sm" pending={add.isPending} isDisabled={!name.trim()} onPress={() => add.mutate(undefined)}>
          Add stage
        </Button>
        {add.error ? <ApiErrorView error={add.error} title="The stage was not added" /> : null}
      </div>
    </div>
  );
}

function Wizard({ tenant }: { tenant: TenantView }) {
  const gates = useCellQuery(GATES_KEY, (api) => api.onboarding.gates(), { refetchInterval: 15_000 });
  const plans = useCellQuery(PLANS_KEY, (api) => api.onboarding.plans());
  const domains = useCellQuery(DOMAINS_KEY, (api) => api.tenant.domains());
  const apps = useCellQuery(APPS_KEY, (api) => api.integration.applications());
  const [report, setReport] = useState<GateReport | null>(null);

  const plan = plans.data?.items.find((p) => p.status !== "SUPERSEDED") ?? null;
  const at = lifecycleIndex(tenant.status);
  const past = (s: string) => at > lifecycleIndex(s);
  const verifiedDomains = domains.data?.items.filter((d) => d.status === "VERIFIED").length ?? 0;
  const activeApps = apps.data?.items.filter((a) => a.status === "ACTIVE" && a.pep_client_id).length ?? 0;

  const refresh = [TENANT_KEY, GATES_KEY, PLANS_KEY];
  const onMoved = (r: GateReport) => setReport(r);
  const approve = useCellMutation((api, id: string) => api.onboarding.approvePlan(plan?.plan_id ?? "", id), { invalidate: refresh, success: "Plan approved" });
  const validate = useCellMutation((api) => api.onboarding.validatePlan(plan?.plan_id ?? ""), { onSuccess: onMoved });
  const integrate = useCellMutation((api) => api.onboarding.startIntegration(plan?.plan_id ?? ""), {
    invalidate: refresh,
    success: "Integration started",
    onSuccess: onMoved,
  });
  const shadow = useCellMutation((api) => api.onboarding.startShadow(plan?.plan_id ?? ""), { invalidate: refresh, success: "Shadow started", onSuccess: onMoved });
  const pilot = useCellMutation((api, id: string) => api.onboarding.startPilot(plan?.plan_id ?? "", id), {
    invalidate: refresh,
    success: "Pilot started",
    onSuccess: onMoved,
  });
  const launch = useCellMutation((api, id: string) => api.onboarding.activate(plan?.plan_id ?? "", id), {
    invalidate: refresh,
    success: "Your company is live",
    onSuccess: onMoved,
  });

  const shown = report?.gates.length ? report.gates : (gates.data?.gates ?? []);
  const blocking = blockingCount(shown);
  const resource = `onboarding_plan:${plan?.plan_id ?? ""}`;
  const progress = Math.max(0, at) / (LIFECYCLE.length - 1);

  const state = (done: boolean, current: boolean, optional = false): StepState => (done ? "done" : current ? "current" : optional ? "optional" : "todo");

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <ol className="space-y-3" aria-label="Onboarding steps">
        <Step
          n={1}
          title="Organization"
          state={state(past("ORGANIZATION_VERIFYING"), at <= lifecycleIndex("ORGANIZATION_VERIFYING"))}
          summary="Company verified against its official register; you are its owner."
        >
          <ButtonLink href="/admin/company-verification" variant="secondary" size="sm">
            Company verification
          </ButtonLink>
        </Step>
        <Step
          n={2}
          title="Domains & residency"
          state={state(verifiedDomains > 0 && past("CONFIGURING"), tenant.status === "CONFIGURING")}
          summary={`${verifiedDomains} verified domain${verifiedDomains === 1 ? "" : "s"}. Residency sets where your data lives.`}
        >
          <div className="flex flex-wrap gap-2">
            <ButtonLink href="/admin/domains" variant="secondary" size="sm">
              Domains
            </ButtonLink>
            <ButtonLink href="/admin/residency" variant="secondary" size="sm">
              Residency
            </ButtonLink>
          </div>
        </Step>
        <Step n={3} title="Identity" state={state(false, false, true)} summary="Connect your identity provider (OIDC or SAML) if people already sign in elsewhere.">
          <ButtonLink href="/admin/identity-providers" variant="secondary" size="sm">
            Identity providers
          </ButtonLink>
        </Step>
        <Step n={4} title="Provisioning" state={state(false, false, true)} summary="Keep people in sync from your directory or HR system with SCIM.">
          <ButtonLink href="/admin/provisioning" variant="secondary" size="sm">
            Provisioning
          </ButtonLink>
        </Step>
        <Step
          n={5}
          title="Applications"
          state={state(activeApps > 0, tenant.status === "INTEGRATING" && activeApps === 0)}
          summary={`${activeApps} active application${activeApps === 1 ? "" : "s"} connected to Zoiko iD.`}
        >
          <ConnectGuide apps={apps.data?.items ?? []} />
        </Step>
        <Step
          n={6}
          title="Policy & plan"
          state={state(plan?.status === "APPROVED", tenant.status === "CONFIGURING" && plan?.status !== "APPROVED")}
          summary={plan ? `Plan v${plan.version}: ${plan.status.toLowerCase()}.` : "Choose how you will use Zoiko iD and approve the onboarding plan."}
        >
          {!plan ? (
            <CreatePlan />
          ) : (
            <div className="space-y-4">
              <PlanSummary plan={plan} />
              {plan.status === "DRAFT" ? (
                <ApprovalStep
                  action={MANAGE}
                  resource={resource}
                  change="APPROVE"
                  label="Approve plan"
                  onRun={(id) => approve.mutate(id)}
                  pending={approve.isPending}
                  error={approve.error}
                />
              ) : null}
              {plan.status === "APPROVED" && tenant.status === "CONFIGURING" ? (
                <Button pending={integrate.isPending} onPress={() => integrate.mutate(undefined)}>
                  Start integrating
                </Button>
              ) : null}
              {integrate.error ? <ApiErrorView error={integrate.error} title="Integration did not start" /> : null}
            </div>
          )}
        </Step>
        <Step
          n={7}
          title="Shadow"
          state={state(past("SHADOW"), tenant.status === "INTEGRATING" || tenant.status === "SHADOW")}
          summary="Zoiko iD evaluates decisions next to your current system without enforcing them."
        >
          <div className="space-y-3">
            {tenant.status === "INTEGRATING" ? (
              <Button pending={shadow.isPending} isDisabled={!plan || activeApps === 0} onPress={() => shadow.mutate(undefined)}>
                Start shadow
              </Button>
            ) : null}
            {tenant.status === "SHADOW" || tenant.status === "PILOT" ? <ShadowProgress /> : null}
            {shadow.error ? <ApiErrorView error={shadow.error} title="Shadow did not start" /> : null}
          </div>
        </Step>
        <Step n={8} title="Pilot" state={state(past("PILOT"), tenant.status === "SHADOW" || tenant.status === "PILOT")} summary="Enforce for an approved, scoped pilot only.">
          {tenant.status === "SHADOW" && plan ? (
            <div className="space-y-3">
              <PilotReadiness />
              <ApprovalStep
              action={ACTIVATE}
              resource={resource}
              change="START_PILOT"
              label="Start pilot"
              onRun={(id) => pilot.mutate(id)}
              pending={pilot.isPending}
              error={pilot.error}
              />
            </div>
          ) : tenant.status === "PILOT" && plan ? (
            <PilotStages plan={plan} />
          ) : (
            <p className="text-sm text-ink-muted">Available once shadow evaluation is running.</p>
          )}
        </Step>
        <Step n={9} title="Launch" state={state(tenant.status === "ACTIVE", tenant.status === "PILOT")} summary="Production enforcement under the approved configuration.">
          {tenant.status === "ACTIVE" ? (
            <Banner tone="ok" icon={Rocket} title="Live">
              Your company is live on Zoiko iD.
            </Banner>
          ) : tenant.status === "PILOT" && plan ? (
            blocking ? (
              <Banner tone="warn" icon={Lock} title="Launch is locked">
                {blocking} blocking gate{blocking === 1 ? "" : "s"} must pass first. There is no launch-anyway bypass.
              </Banner>
            ) : (
              <ApprovalStep
                action={ACTIVATE}
                resource={resource}
                change="ACTIVATE"
                label="Launch"
                onRun={(id) => launch.mutate(id)}
                pending={launch.isPending}
                error={launch.error}
              />
            )
          ) : (
            <p className="text-sm text-ink-muted">Available during the pilot, once every mandatory gate passes.</p>
          )}
        </Step>
      </ol>

      <aside className="space-y-4">
        <Card title="Status">
          <div className="space-y-4">
            <Meter label="Onboarding" value={progress * 100} tone={tenant.status === "ACTIVE" ? "ok" : "brand"} />
            <KeyValues
              columns={1}
              items={[
                ["Stage", <StatusBadge key="s" state={tenant.status} kind="Tenant" />],
                ["Mode", MODES.find((m) => m.id === tenant.deployment_mode)?.label ?? tenant.deployment_mode],
                ["Home cell", <span key="c" className="font-mono">{tenant.home_cell_id}</span>],
              ]}
            />
          </div>
        </Card>
        {plan && (tenant.status === "SHADOW" || tenant.status === "PILOT") ? <Rollback plan={plan} /> : null}
        <Card
          title="Gates"
          description={blocking ? `${blocking} blocking` : "Nothing blocking at this stage."}
          actions={
            plan ? (
              <Button size="sm" variant="secondary" pending={validate.isPending} onPress={() => validate.mutate(undefined)}>
                Validate
              </Button>
            ) : null
          }
        >
          <QueryView query={gates}>{() => <GateList gates={shown} empty="No gates apply at this stage." />}</QueryView>
          {validate.error ? <ApiErrorView error={validate.error} /> : null}
        </Card>
      </aside>
    </div>
  );
}

/** Tenant onboarding console (OVERLAY-001 §25): every step backed by a machine-readable gate. */
export function OnboardingPage() {
  const tenant = useTenant();
  return (
    <div className="space-y-6">
      <PageHeader
        icon={Rocket}
        title="Onboarding"
        description="Set your company up step by step. Each step is checked by the platform; launch stays locked until every mandatory gate passes."
        breadcrumbs={[{ label: "Overview", href: "/overview" }, { label: "Onboarding" }]}
      />
      <QueryView query={tenant}>
        {(t) => {
          if (!t) return <Banner tone="warn" title="No company workspace">You are not part of a company workspace.</Banner>;
          const me = standing(t);
          if (me.pendingOwner || t.status === "DRAFT" || t.status === "ORGANIZATION_VERIFYING") {
            return (
              <Banner tone="info" title="Verify your company first" action={<ButtonLink href="/admin/company-verification" size="sm">Verify company</ButtonLink>}>
                Onboarding starts once <UserText>{t.display_name}</UserText> is verified against its official register and you are confirmed as its owner.
              </Banner>
            );
          }
          if (!me.isAdmin) {
            return <Banner tone="info" title="Administrators only">Onboarding is run by the company&apos;s owners and administrators.</Banner>;
          }
          return (
            <div className="space-y-6">
              <ApprovalsWaiting compact />
              <Wizard tenant={t} />
            </div>
          );
        }}
      </QueryView>
    </div>
  );
}
