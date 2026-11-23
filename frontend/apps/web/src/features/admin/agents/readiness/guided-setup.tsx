"use client";

import type { AgentItem, BlueprintRelease, BuildItem, ToolItem } from "@zoiko/api-client";
import { Badge, Banner, Button, ButtonLink, Card, Checkbox, LoadingRows, PageHeader, TextField } from "@zoiko/ui";
import { useMemo, useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { useMe } from "../../authority/shared";
import { RegisterApiClientDialog } from "../api-agent";
import { manifestDigest } from "../agents-logic";
import { AuthorityNotice, PersonSelect } from "../pickers";
import { AGENTS_KEY, BLUEPRINTS_KEY, BUILDS_KEY, TOOLS_KEY } from "../shared";
import {
  DAARA_BLUEPRINT,
  DAARA_TOOLS,
  GATES,
  GATE_PLAIN,
  chosenBuild,
  inChain,
  nextDaaraVersion,
  planSteps,
  progress,
  stepToShow,
  toolProblem,
  type Step,
  type StepState,
} from "./guided-setup-logic";
import { READINESS_KEY } from "./logic";

const ALL_KEYS = [AGENTS_KEY, BUILDS_KEY, BLUEPRINTS_KEY, TOOLS_KEY, READINESS_KEY];
const SERVER_TYPES = new Set(["APPLICATION", "SERVICE", "WORKLOAD"]);

const BADGE: Record<StepState, { tone: "ok" | "warn" | "neutral"; text: string }> = {
  done: { tone: "ok", text: "Done" },
  you: { tone: "warn", text: "Your turn" },
  waiting: { tone: "neutral", text: "Waiting for someone else" },
  blocked: { tone: "neutral", text: "Later" },
};

type Ctx = {
  me: string;
  tools: ToolItem[];
  build: BuildItem | null;
  release: BlueprintRelease | undefined;
  releases: BlueprintRelease[];
  agent: AgentItem | undefined;
  serverId: string | null;
  organizationId: string | null;
  missingGates: string[];
};

/** The one button (or small form) of a step that is the person's to do. Each runs the same calls the registry pages run; the platform still decides every one. */
function StepAction({ step, ctx, onRegisterClient }: { step: Step; ctx: Ctx; onRegisterClient: () => void }) {
  const [name, setName] = useState("Access readiness");
  const [sponsor, setSponsor] = useState("");
  const [ticked, setTicked] = useState<Record<string, boolean>>({});

  const tools = useCellMutation<void, unknown>(
    async (api) => {
      for (const spec of DAARA_TOOLS) {
        const problem = toolProblem(ctx.tools, spec);
        if (problem === "ok") continue;
        if (problem === "missing") {
          await api.agents.registerTool({
            tool_id: spec.tool_id,
            version: "1.0.0",
            server_principal_id: ctx.serverId ?? "",
            canonical_action: spec.action,
            default_risk_class: 1,
            side_effect: "NONE",
            requires_mandate: false,
            requires_human_approval: false,
          });
        }
        await api.agents.activateTool(spec.tool_id);
      }
      return null;
    },
    { invalidate: ALL_KEYS, success: "Both read-only tools are ready" },
  );
  const approveBuild = useCellMutation<void, unknown>((api) => api.agents.approveBuild(ctx.build?.build_id ?? ""), { invalidate: ALL_KEYS, success: "Key approved" });
  const createRelease = useCellMutation<void, unknown>(
    async (api) => {
      const version = nextDaaraVersion(ctx.releases);
      const used = DAARA_TOOLS.map((s) => ({ tool_id: s.tool_id, version: ctx.tools.find((t) => t.tool_id === s.tool_id)?.version ?? "1.0.0" }));
      return api.agents.createBlueprint({
        blueprint_id: DAARA_BLUEPRINT,
        version,
        artifact_digest: ctx.build?.artifact_digest ?? "",
        tool_manifest_digest: await manifestDigest(used),
        publisher_principal_id: ctx.me,
      });
    },
    { invalidate: ALL_KEYS, success: "Description created" },
  );
  const approveRelease = useCellMutation<void, unknown>((api) => api.agents.approveBlueprint(ctx.release?.blueprint_id ?? "", ctx.release?.version ?? ""), {
    invalidate: ALL_KEYS,
    success: "Description approved",
  });
  const createAgent = useCellMutation<void, unknown>(
    (api) =>
      api.agents.register({
        agent_name: name.trim(),
        blueprint_id: DAARA_BLUEPRINT,
        blueprint_version: ctx.release?.version ?? "",
        operator_principal_id: ctx.me,
        sponsor_principal_id: sponsor || ctx.me,
        owning_organization_id: ctx.organizationId,
        model_provider: "",
        allowed_tools: DAARA_TOOLS.map((t) => t.tool_id),
        max_subagent_depth: 0,
      }),
    { invalidate: ALL_KEYS, success: "Agent created" },
  );
  const bind = useCellMutation<void, unknown>((api) => api.agents.bindApiClient(ctx.agent?.agent_principal_id ?? ""), { invalidate: ALL_KEYS, success: "Key connected" });
  const attest = useCellMutation<void, unknown>(
    async (api) => {
      for (const gate of GATES.filter((g) => ticked[g] && ctx.missingGates.includes(g))) {
        await api.agents.attest(ctx.agent?.agent_principal_id ?? "", gate, GATE_PLAIN[gate].note);
      }
      return null;
    },
    { invalidate: ALL_KEYS, success: "Checks confirmed" },
  );
  const submit = useCellMutation<void, unknown>((api) => api.agents.submit(ctx.agent?.agent_principal_id ?? ""), { invalidate: ALL_KEYS, success: "Sent for activation" });
  const activate = useCellMutation<void, unknown>((api) => api.agents.activate(ctx.agent?.agent_principal_id ?? ""), { invalidate: ALL_KEYS, success: "The agent is active" });

  if (step.state !== "you") return null;
  const agentId = ctx.agent?.agent_principal_id;
  const go = (m: { mutate: (v: undefined) => void }) => () => m.mutate(undefined);

  switch (step.id) {
    case "tools":
      return <Act m={tools} label="Create the two tools" onPress={go(tools)} />;
    case "client":
      return (
        <Button size="lg" variant="primary" onPress={onRegisterClient}>
          Make the key and register it
        </Button>
      );
    case "client_approval":
      return <Act m={approveBuild} label="Approve the key" onPress={go(approveBuild)} />;
    case "release":
      return <Act m={createRelease} label={`Create the description (${DAARA_BLUEPRINT} ${nextDaaraVersion(ctx.releases)})`} onPress={go(createRelease)} />;
    case "release_approval":
      return <Act m={approveRelease} label="Approve the description" onPress={go(approveRelease)} />;
    case "agent":
      return (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim().length >= 2) createAgent.mutate(undefined);
          }}
        >
          <TextField label="Agent name" isRequired value={name} onChange={setName} description="What your team will call it. It can be changed later." />
          <PersonSelect label="Who answers for it (its sponsor)" value={sponsor || ctx.me} onChange={setSponsor} description="An active member of your company. Defaults to you." isRequired />
          <Act m={createAgent} label="Create the agent" onPress={() => name.trim().length >= 2 && createAgent.mutate(undefined)} disabled={name.trim().length < 2} />
        </form>
      );
    case "binding":
      return <Act m={bind} label="Connect the key to the agent" onPress={go(bind)} />;
    case "gates": {
      const mine = GATES.filter((g) => ctx.missingGates.includes(g) && (g !== "SECURITY_REVIEW" || !inChain(ctx.agent, ctx.me)));
      return (
        <fieldset className="space-y-3">
          <legend className="text-sm font-medium text-ink">Tick only what is true. The platform records who confirmed each one.</legend>
          {mine.map((g) => (
            <Checkbox key={g} isSelected={Boolean(ticked[g])} onChange={(on) => setTicked((t) => ({ ...t, [g]: on }))}>
              <span className="font-medium">{GATE_PLAIN[g].title}</span>
              <span className="block text-xs text-ink-muted">{GATE_PLAIN[g].note}</span>
            </Checkbox>
          ))}
          <Act m={attest} label="Confirm the ticked checks" onPress={go(attest)} disabled={!mine.some((g) => ticked[g])} />
        </fieldset>
      );
    }
    case "activation":
      return ctx.agent?.status === "DRAFT" ? (
        <Act m={submit} label="Send for activation" onPress={go(submit)} />
      ) : (
        <Act m={activate} label="Switch the agent on" onPress={go(activate)} />
      );
    case "setup":
      return (
        <ButtonLink size="lg" variant="primary" href="/admin/agents/readiness/new">
          Open the setup
        </ButtonLink>
      );
    case "publish":
      return agentId ? (
        <ButtonLink size="lg" variant="primary" href={`/admin/agents/readiness/${encodeURIComponent(agentId)}/setup`}>
          Open the setup to approve it
        </ButtonLink>
      ) : null;
    case "run":
      return (
        <ButtonLink size="lg" variant="primary" href="/admin/agents/readiness">
          Go to the agent to run it
        </ButtonLink>
      );
    default:
      return null;
  }
}

function Act({ m, label, onPress, disabled }: { m: { isPending: boolean; error: unknown }; label: string; onPress: () => void; disabled?: boolean }) {
  return (
    <div className="space-y-2">
      <Button size="lg" variant="primary" pending={m.isPending} isDisabled={disabled} onPress={onPress}>
        {label}
      </Button>
      {m.error ? <ApiErrorView error={m.error} /> : null}
    </div>
  );
}

function StepRow({ n, step, current }: { n: number; step: Step; current: boolean }) {
  const b = BADGE[step.state];
  return (
    <li className={`rounded-card border p-3 ${current ? "border-brand-500 bg-brand-50/40" : "border-border"}`} aria-current={current ? "step" : undefined}>
      <details open={current}>
        <summary className="flex min-h-11 cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span className="font-medium text-ink">
            {n}. {step.title}
          </span>
          <Badge tone={b.tone}>{b.text}</Badge>
        </summary>
        <div className="mt-2 space-y-1 pl-1 text-sm text-ink-muted">
          <p>{step.why}</p>
          {step.state === "done" ? step.doneText ? <p className="font-medium text-ink">{step.doneText}</p> : null : step.detail ? <p className="text-ink">{step.detail}</p> : null}
        </div>
      </details>
    </li>
  );
}

/** One page for the whole job: where the company is, whose turn it is, and the next button. Both people open this same page; each finds their own step. */
export function GuidedSetupPage() {
  const me = useMe();
  const tools = useCellQuery(TOOLS_KEY, (api) => api.agents.tools());
  const builds = useCellQuery(BUILDS_KEY, (api) => api.agents.builds());
  const releases = useCellQuery(BLUEPRINTS_KEY, (api) => api.agents.blueprints());
  const agents = useCellQuery(AGENTS_KEY, (api) => api.agents.list());
  const setups = useCellQuery(READINESS_KEY, (api) => api.accessReadiness.setups());
  const principals = useCellQuery(["admin-picker", "principals"], (api) => api.principals.list({ limit: 200 }));
  const company = useCellQuery(["tenant", "me"], (api) => api.tenant.me());
  const agent = (agents.data?.items ?? []).find((a) => a.blueprint_id === DAARA_BLUEPRINT && a.status !== "REVOKED" && a.status !== "RETIRED");
  const agentId = agent?.agent_principal_id ?? "";
  const detail = useCellQuery([...AGENTS_KEY, "detail", agentId], (api) => api.agents.get(agentId), { enabled: Boolean(agentId) });
  const binding = useCellQuery([...AGENTS_KEY, "api-binding", agentId], (api) => api.agents.apiBinding(agentId), { enabled: Boolean(agentId) });
  const [registering, setRegistering] = useState(false);

  const ready = [tools, builds, releases, agents, setups, principals].every((q) => q.isSuccess || q.isError) && Boolean(me);
  const serverId = (principals.data?.items ?? []).find((p) => SERVER_TYPES.has(p.principal_type) && p.status === "ACTIVE")?.principal_id ?? null;
  const steps = useMemo(
    () =>
      planSteps({
        me: me ?? "",
        tools: tools.data?.items ?? [],
        builds: builds.data?.items ?? [],
        releases: releases.data?.items ?? [],
        agents: agents.data?.items ?? [],
        detail: detail.data ?? null,
        binding: binding.data ?? null,
        setups: setups.data?.items ?? [],
        serverId,
      }),
    [me, tools.data, builds.data, releases.data, agents.data, detail.data, binding.data, setups.data, serverId],
  );
  const current = stepToShow(steps);
  const { done, total } = progress(steps);
  const build = chosenBuild(builds.data?.items ?? [], releases.data?.items ?? []);
  const release = (releases.data?.items ?? []).find((r) => r.blueprint_id === DAARA_BLUEPRINT && r.review_status !== "REVOKED" && (!build || r.artifact_digest === build.artifact_digest));
  const attested = Object.keys(detail.data?.readiness.attestations ?? {});
  const ctx: Ctx = {
    me: me ?? "",
    tools: tools.data?.items ?? [],
    build,
    release,
    releases: releases.data?.items ?? [],
    agent,
    serverId,
    organizationId: company.data?.organization_id ?? null,
    missingGates: GATES.filter((g) => !attested.includes(g)),
  };

  return (
    <>
      <PageHeader
        title="Set up the access readiness agent"
        description="Checks your company's mandates against fixed rules and suggests reviews for a person. It never changes anyone's access."
        breadcrumbs={[{ label: "Agents & tools", href: "/admin/agents" }, { label: "Access readiness", href: "/admin/agents/readiness" }, { label: "Guided setup" }]}
      />
      <div className="space-y-5">
        <Banner tone="info" title="How this works">
          <p>
            Setting this up takes {total} short steps and <strong>two people</strong>, because the platform never lets the person who builds an agent approve it. You can leave and come back at any time:
            this page works out where you are. A colleague opens the same page and sees their own next step.
          </p>
        </Banner>
        {!ready ? (
          <LoadingRows rows={4} />
        ) : (
          <>
            <div>
              <p className="mb-1 text-sm font-medium text-ink" id="guided-progress">
                {done} of {total} steps done
              </p>
              <progress aria-labelledby="guided-progress" className="h-2 w-full" max={total} value={done} />
            </div>
            {current ? (
              <Card title={current.title} description={current.state === "you" ? "Your turn" : BADGE[current.state].text}>
                <div className="space-y-3 text-sm">
                  <p className="text-ink-muted">{current.why}</p>
                  {current.detail ? <p className="text-ink">{current.detail}</p> : null}
                  {current.right ? <AuthorityNotice needs={[current.right]} what="do this step" /> : null}
                  <StepAction step={current} ctx={ctx} onRegisterClient={() => setRegistering(true)} />
                  {current.state === "waiting" ? (
                    <Banner tone="info" title="Nothing for you to press here">
                      Someone else has to do this step. Send them to this page.
                    </Banner>
                  ) : null}
                </div>
              </Card>
            ) : (
              <Banner tone="ok" title="Everything is set up">
                The agent is ready. Go to the agent to run it and read what it finds.
              </Banner>
            )}
            <Card title="All the steps" description="Open any step to read why it exists.">
              <ol className="space-y-2">
                {steps.map((s, i) => (
                  <StepRow key={s.id} n={i + 1} step={s} current={s.id === current?.id} />
                ))}
              </ol>
            </Card>
          </>
        )}
      </div>
      <RegisterApiClientDialog isOpen={registering} onClose={() => setRegistering(false)} />
    </>
  );
}
