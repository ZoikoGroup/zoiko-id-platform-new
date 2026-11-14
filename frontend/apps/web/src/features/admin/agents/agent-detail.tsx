"use client";

import type { AgentGate } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, CopyId, Dialog, KeyValues, Select, StatusBadge, TextField } from "@zoiko/ui";
import { CheckCircle2, CircleDashed, X } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { GATE_NOTES } from "./agents-logic";
import { ApiBindingCard } from "./api-agent";
import { RegisterChildDialog } from "./register-child-dialog";
import { AGENTS_KEY, BLOCKER_TEXT, GATES, PLATFORM_CHECKS } from "./shared";

type Action = "submit" | "activate" | "suspend" | "revoke" | "retire";

const ACTION_COPY: Record<Action, { title: string; body: string; cta: string; danger?: boolean; reason?: boolean }> = {
  submit: { title: "Submit for approval?", body: "Moves the agent from draft to pending approval. Nothing runs yet.", cta: "Submit" },
  activate: {
    title: "Activate this agent?",
    body: "All ten activation gates must hold. The agent can then obtain short-lived workload credentials, but it still acts only through grants and mandates.",
    cta: "Activate agent",
  },
  suspend: {
    title: "Suspend this agent?",
    body: "The kill path: the agent, every sub-agent below it and their live runtime bindings stop now. Credentials already issued are cut off at the next check. Coming back needs re-validation.",
    cta: "Suspend now",
    danger: true,
    reason: true,
  },
  revoke: {
    title: "Revoke this agent permanently?",
    body: "Revocation is terminal and cannot be undone. Its runtime bindings are revoked, its sub-agents are suspended for review, and its history is kept as evidence.",
    cta: "Revoke permanently",
    danger: true,
    reason: true,
  },
  retire: { title: "Retire this agent?", body: "A planned end of life. The identity is kept for history but never runs again.", cta: "Retire agent", danger: true },
};

export function AgentDetailPanel({ agentId, onClose }: { agentId: string; onClose: () => void }) {
  const key = [...AGENTS_KEY, "detail", agentId];
  const detail = useCellQuery(key, (api) => api.agents.get(agentId));
  const [action, setAction] = useState<Action | null>(null);
  const [reason, setReason] = useState("OPERATOR_DECISION");
  const [attesting, setAttesting] = useState<AgentGate | null>(null);
  const [note, setNote] = useState("");
  const [addingChild, setAddingChild] = useState(false);

  const act = useCellMutation(
    (api, v: { action: Action; reason: string }): Promise<unknown> => {
      switch (v.action) {
        case "submit":
          return api.agents.submit(agentId);
        case "activate":
          return api.agents.activate(agentId);
        case "suspend":
          return api.agents.suspend(agentId, v.reason);
        case "revoke":
          return api.agents.revoke(agentId, v.reason);
        case "retire":
          return api.agents.retire(agentId);
      }
    },
    { invalidate: [AGENTS_KEY], success: "Done", onSuccess: () => setAction(null) },
  );
  const attest = useCellMutation((api, v: { gate: AgentGate; note: string }) => api.agents.attest(agentId, v.gate, v.note), {
    invalidate: [AGENTS_KEY],
    success: "Attestation recorded",
    onSuccess: () => {
      setAttesting(null);
      setNote("");
    },
  });

  const closeAction = () => {
    if (act.isPending) return;
    setAction(null);
    act.reset();
  };
  const closeAttest = () => {
    if (attest.isPending) return;
    setAttesting(null);
    attest.reset();
  };

  return (
    <section aria-labelledby="agent-detail-title" className="space-y-4">
      <QueryView query={detail}>
        {(a) => {
          const r = a.readiness;
          const blocked = new Set(r.blockers);
          const canSubmit = a.status === "DRAFT";
          const canActivate = a.status === "PENDING_APPROVAL" || a.status === "SUSPENDED";
          const live = a.status === "ACTIVE";
          const open = a.status !== "REVOKED" && a.status !== "RETIRED";
          const copy = action ? ACTION_COPY[action] : null;
          return (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <h2 id="agent-detail-title" className="text-base font-semibold text-ink">
                    {a.agent_name}
                  </h2>
                  <div className="mt-0.5 flex flex-wrap items-center gap-2">
                    <CopyId value={a.agent_principal_id} short={false} label="agent principal ID" />
                    <StatusBadge state={a.status} kind="Agent status" size="xs" />
                    {a.parent_agent_id ? <Badge size="xs" tone="info">Sub-agent</Badge> : null}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {canSubmit ? <Button size="sm" onPress={() => setAction("submit")}>Submit for approval</Button> : null}
                  {canActivate ? <Button size="sm" onPress={() => setAction("activate")}>Activate</Button> : null}
                  {live && a.max_subagent_depth > 0 ? (
                    <Button size="sm" variant="secondary" onPress={() => setAddingChild(true)}>Add sub-agent</Button>
                  ) : null}
                  {live ? <Button size="sm" variant="danger-outline" onPress={() => setAction("suspend")}>Suspend</Button> : null}
                  {open ? <Button size="sm" variant="danger-outline" onPress={() => setAction("revoke")}>Revoke</Button> : null}
                  {live || a.status === "SUSPENDED" ? <Button size="sm" variant="secondary" onPress={() => setAction("retire")}>Retire</Button> : null}
                  <Button size="sm" variant="ghost" icon={<X aria-hidden className="size-4" />} onPress={onClose}>Close</Button>
                </div>
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <Card title="Accountability" description="Four different roles. They are never collapsed into one owner.">
                  <KeyValues
                    items={[
                      ["Sponsor (accountable person)", a.sponsor_principal_id ? <CopyId key="s" value={a.sponsor_principal_id} label="sponsor" /> : "None"],
                      ["Owning organization", a.owning_organization_id ? <CopyId key="o" value={a.owning_organization_id} label="organization" /> : "None"],
                      ["Operator (runs it)", <CopyId key="op" value={a.operator_principal_id} label="operator" />],
                      ["Publisher (built it)", <CopyId key="p" value={a.publisher_principal_id} label="publisher" />],
                      ["Runtime workloads", `${r.live_bindings} live binding${r.live_bindings === 1 ? "" : "s"}`],
                      ["Blueprint", <span key="b" className="font-mono text-xs">{a.blueprint_id}@{a.blueprint_version}</span>],
                      ["Model (metadata only)", a.model_provider || "—"],
                      ["Sub-agents", `${a.sub_agents} (max depth ${a.max_subagent_depth})`],
                      ["Security revision", String(a.security_revision)],
                    ]}
                  />
                  <p className="mt-3 text-xs text-ink-muted">
                    Allowed tools (a mandate can narrow these further):{" "}
                    {a.allowed_tools.length ? a.allowed_tools.map((t) => <code key={t} className="mr-1 font-mono">{t}</code>) : "none"}
                  </p>
                </Card>

                <Card title="Activation checklist" description="Ten gates must hold before an agent can run. Four are attested by a person; the rest the platform checks.">
                  <ul className="space-y-2 text-sm">
                    {PLATFORM_CHECKS.map((c) => (
                      <li key={c.code} className="flex items-start gap-2">
                        {blocked.has(c.code) ? <CircleDashed aria-hidden className="mt-0.5 size-4 text-warn-fg" /> : <CheckCircle2 aria-hidden className="mt-0.5 size-4 text-ok-fg" />}
                        <span>
                          {c.label}
                          <span className="sr-only">{blocked.has(c.code) ? " (not met)" : " (met)"}</span>
                          {blocked.has(c.code) ? <span className="block text-xs text-ink-muted">{BLOCKER_TEXT[c.code]}</span> : null}
                        </span>
                      </li>
                    ))}
                    {GATES.map((g) => {
                      const done = r.attestations[g.id];
                      return (
                        <li key={g.id} className="flex items-start gap-2">
                          {done ? <CheckCircle2 aria-hidden className="mt-0.5 size-4 text-ok-fg" /> : <CircleDashed aria-hidden className="mt-0.5 size-4 text-warn-fg" />}
                          <span className="flex-1">
                            {g.label}
                            <span className="sr-only">{done ? " (attested)" : " (not attested)"}</span>
                            <span className="block text-xs text-ink-muted">
                              {done ? `Attested ${new Date(done.attested_at).toLocaleString()}${done.note ? `: ${done.note}` : ""}` : g.help}
                            </span>
                          </span>
                          {open ? (
                            <Button size="xs" variant="secondary" onPress={() => setAttesting(g.id)}>
                              {done ? "Re-attest" : "Attest"}
                            </Button>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </Card>
              </div>

              <Dialog
                isOpen={action !== null}
                onOpenChange={(o) => !o && closeAction()}
                title={copy?.title ?? ""}
                size="sm"
                role="alertdialog"
                footer={
                  <>
                    <Button variant="secondary" isDisabled={act.isPending} onPress={closeAction}>Cancel</Button>
                    <Button
                      variant={copy?.danger ? "danger" : "primary"}
                      pending={act.isPending}
                      onPress={() => action && act.mutate({ action, reason })}
                    >
                      {copy?.cta}
                    </Button>
                  </>
                }
              >
                <div className="space-y-3 text-sm text-ink-muted">
                  <p>{copy?.body}</p>
                  {action === "activate" && r.blockers.length ? (
                    <Banner tone="warn" title="Still blocking">
                      <ul className="list-disc pl-4">
                        {r.blockers.map((b) => (
                          <li key={b}>{BLOCKER_TEXT[b] ?? b}</li>
                        ))}
                      </ul>
                    </Banner>
                  ) : null}
                  {copy?.reason ? (
                    <TextField label="Reason code" mono value={reason} onChange={setReason} description="Capital letters, digits and underscores; recorded with the evidence." />
                  ) : null}
                  {act.error ? <ApiErrorView error={act.error} /> : null}
                </div>
              </Dialog>

              <Dialog
                isOpen={attesting !== null}
                onOpenChange={(o) => !o && closeAttest()}
                title={`Attest: ${GATES.find((g) => g.id === attesting)?.label ?? ""}`}
                description="You are recording that this is true. Your identity and the time are kept as evidence."
                size="sm"
                footer={
                  <>
                    <Button variant="secondary" isDisabled={attest.isPending} onPress={closeAttest}>Cancel</Button>
                    <Button pending={attest.isPending} onPress={() => attesting && attest.mutate({ gate: attesting, note })}>Record attestation</Button>
                  </>
                }
              >
                <div className="space-y-3">
                  <p className="text-sm text-ink-muted">{GATES.find((g) => g.id === attesting)?.help}</p>
                  {attesting ? (
                    <Select
                      label="Suggested note"
                      placeholder="Pick what was checked…"
                      value={null}
                      onChange={(id) => setNote(GATE_NOTES[attesting].find((n) => n.id === id)?.label ?? note)}
                      options={GATE_NOTES[attesting]}
                      description="Fills the note below. You can edit it."
                    />
                  ) : null}
                  <TextField label="Note (optional)" multiline rows={3} value={note} onChange={setNote} description="What was reviewed or tested, for the next reader." />
                  {attest.error ? <ApiErrorView error={attest.error} /> : null}
                </div>
              </Dialog>
              <ApiBindingCard agentId={agentId} />
              {addingChild ? <RegisterChildDialog parent={a} onClose={() => setAddingChild(false)} /> : null}
            </>
          );
        }}
      </QueryView>
    </section>
  );
}
