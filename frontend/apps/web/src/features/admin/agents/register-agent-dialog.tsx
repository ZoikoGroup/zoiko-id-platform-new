"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Banner, Button, Dialog, Select, TextField, useToast } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { PrincipalField, useMe } from "../authority/shared";
import { MODEL_PROVIDERS, NEEDS } from "./agents-logic";
import { AuthorityNotice, PersonSelect, ToolChecklist } from "./pickers";
import { AGENTS_KEY, BLUEPRINTS_KEY, TOOLS_KEY, UUID_RE } from "./shared";

export function RegisterAgentDialog({ isOpen, onClose, onCreated }: { isOpen: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const { api, protect } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();
  const me = useMe();
  const blueprints = useCellQuery(BLUEPRINTS_KEY, (a) => a.agents.blueprints(), { enabled: isOpen });
  const tools = useCellQuery(TOOLS_KEY, (a) => a.agents.tools(), { enabled: isOpen });
  const company = useCellQuery(["tenant", "me"], (a) => a.tenant.me(), { enabled: isOpen });

  const [name, setName] = useState("");
  const [blueprint, setBlueprint] = useState("");
  const [sponsorPick, setSponsorPick] = useState("");
  const [operatorPick, setOperatorPick] = useState("");
  const [orgPick, setOrgPick] = useState("");
  const [model, setModel] = useState("");
  const [toolIds, setToolIds] = useState<string[]>([]);
  const [depth, setDepth] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<string | null>(null);

  // The accountable person and the operator default to the person registering; the owning organization is the company itself.
  const sponsor = sponsorPick || me || "";
  const operator = operatorPick || me || "";
  const companyId = company.data?.organization_id ?? "";
  const org = orgPick || companyId;

  const approved = (blueprints.data?.items ?? []).filter((b) => b.review_status === "APPROVED");
  const options = approved.map((b) => ({ id: `${b.blueprint_id}@${b.version}`, label: `${b.blueprint_id} @ ${b.version}` }));
  // A freshly approved blueprint is the one most people want: offer it without a click when there is exactly one.
  const chosenBlueprint = blueprint || (options.length === 1 ? (options[0]?.id ?? "") : "");

  function close() {
    if (pending) return;
    setError(null);
    setFormError(null);
    onClose();
  }

  async function submit() {
    setFormError(null);
    setError(null);
    const [blueprintId, version] = chosenBlueprint.split("@");
    if (name.trim().length < 2) return setFormError("Give the agent a name (a label, not an identifier).");
    if (!blueprintId || !version) return setFormError("Choose an approved blueprint release.");
    if (!UUID_RE.test(sponsor.trim())) return setFormError("Choose the sponsor: the person accountable for this agent.");
    if (!UUID_RE.test(operator.trim())) return setFormError("Choose the operator: who runs the deployed agent.");
    if (org.trim() && !UUID_RE.test(org.trim())) return setFormError("The owning organization must be an organization ID.");
    if (!toolIds.length) return setFormError("Tick at least one tool the agent may ever use.");
    setPending(true);
    try {
      const created = await protect(() =>
        api.agents.register({
          agent_name: name.trim(),
          blueprint_id: blueprintId,
          blueprint_version: version,
          operator_principal_id: operator.trim(),
          sponsor_principal_id: sponsor.trim(),
          owning_organization_id: org.trim() || null,
          model_provider: model,
          allowed_tools: toolIds,
          max_subagent_depth: depth,
        }),
      );
      await qc.invalidateQueries({ queryKey: AGENTS_KEY });
      toast.notify({ tone: "ok", title: "Agent registered as a draft", body: "Complete the activation checklist to run it." });
      onCreated(created.agent_principal_id);
      close();
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title="Register agent"
      description="Creates a draft identity. It cannot run until every activation gate holds."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={pending} onPress={close}>Cancel</Button>
          <Button pending={pending} onPress={() => void submit()}>Register agent</Button>
        </>
      }
    >
      <div className="space-y-4">
        <AuthorityNotice needs={NEEDS.agent} what="register an agent" />
        <TextField label="Agent name" isRequired value={name} onChange={setName} placeholder="Acme Invoice Agent" description="A label people recognise; the identity itself is generated." />
        <Select
          label="Blueprint release"
          isRequired
          placeholder={options.length ? "Choose the design it is built from…" : "No approved blueprint yet"}
          options={options}
          value={chosenBlueprint || null}
          onChange={setBlueprint}
          description={options.length ? "Only approved releases can be used." : "Draft a blueprint under Blueprints; a second person approves it."}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <PersonSelect label="Sponsor" isRequired value={sponsor} onChange={setSponsorPick} description="The accountable person." />
          <PersonSelect label="Operator" isRequired value={operator} onChange={setOperatorPick} description="Who runs the deployed agent." />
        </div>
        {companyId ? (
          <p className="text-sm text-ink-muted">
            Owning organization: <span className="font-medium text-ink">{company.data?.display_name}</span>
          </p>
        ) : company.isSuccess ? (
          <PrincipalField label="Owning organization" value={orgPick} onChange={setOrgPick} preferTypes={["ORGANIZATION"]} description="Required to activate." />
        ) : null}
        <div className="space-y-2">
          <p className="text-sm font-medium text-ink">
            Tools it may use <span aria-hidden className="text-danger-fg">*</span>
          </p>
          <p className="text-xs text-ink-subtle">The most this agent may ever use; a mandate can narrow it further. A draft tool can be ticked now and activated later.</p>
          <ToolChecklist tools={tools.data?.items ?? []} value={toolIds} onChange={setToolIds} emptyHint="No tools yet. Register one under Tools first." />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Model provider"
            placeholder="Not stated"
            options={MODEL_PROVIDERS}
            value={model || null}
            onChange={setModel}
            description="For your records only. Never part of identity or authority."
          />
          <Select
            label="Maximum sub-agent depth"
            options={[0, 1, 2, 3, 4, 5].map((n) => ({ id: String(n), label: n === 0 ? "0 (no sub-agents)" : String(n) }))}
            value={String(depth)}
            onChange={(v) => setDepth(Number(v))}
            description="A sub-agent can never be deeper than its parent allows."
          />
        </div>
        {formError ? <Banner tone="danger" title="Check the form" live="polite">{formError}</Banner> : null}
        {error ? <ApiErrorView error={error} /> : null}
      </div>
    </Dialog>
  );
}
