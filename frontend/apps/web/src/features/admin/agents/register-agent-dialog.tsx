"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Banner, Button, Dialog, Select, TextField, useToast } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { AGENTS_KEY, BLUEPRINTS_KEY, splitList, TOOLS_KEY, UUID_RE } from "./shared";

export function RegisterAgentDialog({ isOpen, onClose, onCreated }: { isOpen: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const { api, protect } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();
  const blueprints = useCellQuery(BLUEPRINTS_KEY, (a) => a.agents.blueprints(), { enabled: isOpen });
  const tools = useCellQuery(TOOLS_KEY, (a) => a.agents.tools(), { enabled: isOpen });

  const [name, setName] = useState("");
  const [blueprint, setBlueprint] = useState("");
  const [sponsor, setSponsor] = useState("");
  const [operator, setOperator] = useState("");
  const [org, setOrg] = useState("");
  const [model, setModel] = useState("");
  const [toolIds, setToolIds] = useState("");
  const [depth, setDepth] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const approved = (blueprints.data?.items ?? []).filter((b) => b.review_status === "APPROVED");
  const options = approved.map((b) => ({ id: `${b.blueprint_id}@${b.version}`, label: `${b.blueprint_id} @ ${b.version}` }));
  const registered = (tools.data?.items ?? []).map((t) => t.tool_id);

  function close() {
    if (pending) return;
    setError(null);
    setFormError(null);
    onClose();
  }

  async function submit() {
    setFormError(null);
    setError(null);
    const [blueprintId, version] = blueprint.split("@");
    if (name.trim().length < 2) return setFormError("Give the agent a name (a label, not an identifier).");
    if (!blueprintId || !version) return setFormError("Choose an approved blueprint release.");
    if (!UUID_RE.test(sponsor.trim())) return setFormError("The sponsor must be a principal ID (a person or organization who is accountable).");
    if (!UUID_RE.test(operator.trim())) return setFormError("The operator must be a principal ID.");
    if (org.trim() && !UUID_RE.test(org.trim())) return setFormError("The owning organization must be an organization ID.");
    const list = splitList(toolIds);
    const unknown = list.filter((t) => !registered.includes(t));
    if (unknown.length) return setFormError(`Not registered: ${unknown.join(", ")}. Register the tool first.`);
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
          model_provider: model.trim(),
          allowed_tools: list,
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
        <TextField label="Agent name" isRequired value={name} onChange={setName} description="A human label; the identity is the generated principal ID." />
        <Select
          label="Blueprint release"
          options={options}
          value={blueprint}
          onChange={setBlueprint}
          description={options.length ? "Only approved releases can be used." : "No approved blueprint yet: draft and approve one under Blueprints."}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Sponsor principal ID" isRequired mono value={sponsor} onChange={setSponsor} description="The accountable person or organization." />
          <TextField label="Operator principal ID" isRequired mono value={operator} onChange={setOperator} description="Who runs the deployed agent." />
        </div>
        <TextField label="Owning organization ID" mono value={org} onChange={setOrg} description="Required to activate. The publisher comes from the blueprint." />
        <TextField label="Model provider (metadata)" value={model} onChange={setModel} description="Never part of identity or authority." />
        <TextField
          label="Allowed tools"
          mono
          value={toolIds}
          onChange={setToolIds}
          description={`The most this agent may ever use; a mandate can narrow it. Registered: ${registered.join(", ") || "none yet"}.`}
        />
        <Select
          label="Maximum sub-agent depth"
          options={[0, 1, 2, 3, 4, 5].map((n) => ({ id: String(n), label: n === 0 ? "0 (no sub-agents)" : String(n) }))}
          value={String(depth)}
          onChange={(v) => setDepth(Number(v))}
          description="A sub-agent can never be deeper than its parent allows."
        />
        {formError ? <Banner tone="danger" title="Check the form" live="polite">{formError}</Banner> : null}
        {error ? <ApiErrorView error={error} /> : null}
      </div>
    </Dialog>
  );
}
