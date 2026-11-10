"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { AgentItem } from "@zoiko/api-client";
import { Banner, Button, Dialog, Select, TextField, useToast } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { NEEDS } from "./agents-logic";
import { AuthorityNotice, PersonSelect, ToolChecklist } from "./pickers";
import { AGENTS_KEY, TOOLS_KEY, UUID_RE } from "./shared";

/** Registers a sub-agent under a running agent. It can never hold more than its parent: its tools are a subset of
 * the parent's and it can go no deeper than the parent allows. It starts as a draft like any other agent. */
export function RegisterChildDialog({ parent, onClose }: { parent: AgentItem; onClose: () => void }) {
  const { api, protect } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();
  const tools = useCellQuery(TOOLS_KEY, (a) => a.agents.tools());
  const [name, setName] = useState("");
  const [sponsorPick, setSponsorPick] = useState("");
  const [operatorPick, setOperatorPick] = useState("");
  const [toolIds, setToolIds] = useState<string[]>([]);
  const [depth, setDepth] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const maxDepth = Math.max(parent.max_subagent_depth - 1, 0);
  // The same people as the parent unless changed; only the parent's own tools can be ticked.
  const sponsor = sponsorPick || parent.sponsor_principal_id || "";
  const operator = operatorPick || parent.operator_principal_id;
  const parentTools = (tools.data?.items ?? []).filter((t) => parent.allowed_tools.includes(t.tool_id));

  async function submit() {
    setFormError(null);
    setError(null);
    const outside = toolIds.filter((t) => !parent.allowed_tools.includes(t));
    if (name.trim().length < 2) return setFormError("Give the sub-agent a name (a label, not an identifier).");
    if (!UUID_RE.test(sponsor.trim()) || !UUID_RE.test(operator.trim())) return setFormError("Choose the sponsor and the operator.");
    if (!toolIds.length) return setFormError("Tick at least one tool.");
    if (outside.length) return setFormError(`The parent cannot delegate ${outside.join(", ")}: a sub-agent never gets more than its parent.`);
    setPending(true);
    try {
      await protect(() =>
        api.agents.registerChild(parent.agent_principal_id, {
          agent_name: name.trim(),
          operator_principal_id: operator.trim(),
          sponsor_principal_id: sponsor.trim(),
          allowed_tools: toolIds,
          max_subagent_depth: depth,
        }),
      );
      await qc.invalidateQueries({ queryKey: AGENTS_KEY });
      toast.notify({ tone: "ok", title: "Sub-agent registered as a draft", body: "It needs its own activation checks before it can run." });
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !pending && onClose()}
      title="Register sub-agent"
      description={`Under ${parent.agent_name}. It never holds more than its parent: tools are a subset, depth is smaller.`}
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={pending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={pending} onPress={() => void submit()}>
            Register sub-agent
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <AuthorityNotice needs={NEEDS.agent} what="register an agent" />
        <TextField label="Sub-agent name" isRequired value={name} onChange={setName} />
        <div className="grid gap-4 sm:grid-cols-2">
          <PersonSelect label="Sponsor" isRequired value={sponsor} onChange={setSponsorPick} description="Same as the parent unless you change it." />
          <PersonSelect label="Operator" isRequired value={operator} onChange={setOperatorPick} description="Same as the parent unless you change it." />
        </div>
        <div className="space-y-2">
          <p className="text-sm font-medium text-ink">
            Tools <span aria-hidden className="text-danger-fg">*</span>
          </p>
          <p className="text-xs text-ink-subtle">Only the parent&apos;s tools can be given on.</p>
          <ToolChecklist tools={parentTools} value={toolIds} onChange={setToolIds} emptyHint="The parent has no registered tools to give on." />
        </div>
        <Select
          label="Maximum sub-agent depth"
          options={Array.from({ length: maxDepth + 1 }, (_, n) => ({ id: String(n), label: n === 0 ? "0 (no further sub-agents)" : String(n) }))}
          value={String(depth)}
          onChange={(v) => setDepth(Number(v))}
          description={`At most ${maxDepth}: the parent allows ${parent.max_subagent_depth}.`}
        />
        {formError ? (
          <Banner tone="danger" title="Check the form" live="polite">
            {formError}
          </Banner>
        ) : null}
        {error ? <ApiErrorView error={error} /> : null}
      </div>
    </Dialog>
  );
}
