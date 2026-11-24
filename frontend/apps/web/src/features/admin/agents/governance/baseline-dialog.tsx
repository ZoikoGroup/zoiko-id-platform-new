"use client";

import type { GovBaseline } from "@zoiko/api-client";
import { Banner, Button, Dialog, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { ToolChecklist } from "../pickers";
import { TOOLS_KEY, splitList } from "../shared";
import { GOV_KEY } from "./logic";

/** What an Owner may save: tools that exist and are registered, exact regions (no wildcards). Everything else on the baseline is kept as it is. */
export function baselineBody(
  current: Partial<GovBaseline> | null | undefined,
  tools: string[],
  regionsText: string,
) {
  return {
    allowed_tools: [...new Set(tools)],
    allowed_regions: [...new Set(splitList(regionsText))],
    ai_providers: current?.ai_providers ?? [],
    quotas: current?.quotas && Object.keys(current.quotas).length ? current.quotas : null,
    schedules_allowed: current?.schedules_allowed ?? false,
  };
}

export function regionProblem(regionsText: string): string | null {
  const regions = splitList(regionsText);
  if (!regions.length) return "Name at least one region, for example local-1. Without a region no agent can run.";
  if (regions.some((r) => r.includes("*"))) return "A region is an exact name. Wildcards are refused.";
  return null;
}

/** The company baseline (CR-105): the tools and regions a company allows any agent. With none, no agent can run. Needs agent.governance.manage. */
export function BaselineDialog({
  isOpen,
  onClose,
  current,
}: {
  isOpen: boolean;
  onClose: () => void;
  current: Partial<GovBaseline> | null | undefined;
}) {
  // Mounted only while open, so the form always starts from the baseline as it is now.
  return isOpen ? <BaselineForm onClose={onClose} current={current} /> : null;
}

function BaselineForm({ onClose, current }: { onClose: () => void; current: Partial<GovBaseline> | null | undefined }) {
  const tools = useCellQuery(TOOLS_KEY, (a) => a.agents.tools());
  const [picked, setPicked] = useState<string[]>(current?.allowed_tools ?? []);
  const [regions, setRegions] = useState((current?.allowed_regions ?? []).join(", "));
  const [formError, setFormError] = useState<string | null>(null);

  const save = useCellMutation<void, unknown>(
    (api) => api.agentGovernance.setBaseline(baselineBody(current, picked, regions)),
    { invalidate: [GOV_KEY], success: "Company baseline saved", onSuccess: () => onClose() },
  );

  function submit() {
    const problem = regionProblem(regions);
    if (problem) return setFormError(problem);
    if (!picked.length) return setFormError("Choose at least one tool the company allows.");
    setFormError(null);
    save.mutate();
  }

  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title="Company baseline"
      description="The most any agent in this company may use. A policy can narrow it, never go beyond it."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={save.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={save.isPending} onPress={submit}>
            Save baseline
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Banner tone="info" title="Who can do this">
          An Owner with the agent governance manage right. Quotas, AI providers and the schedule switch are kept as they are. Limits can only be lowered below the platform ceiling, never raised.
        </Banner>
        <div className="space-y-2">
          <p className="text-sm font-medium text-ink">Tools the company allows</p>
          <ToolChecklist
            tools={tools.data?.items ?? []}
            value={picked}
            onChange={setPicked}
            emptyHint="No tool is registered yet. Register the tools under Agents and tools, Tools, first."
          />
        </div>
        <TextField
          label="Regions"
          isRequired
          mono
          value={regions}
          onChange={setRegions}
          placeholder="local-1"
          description="The cells agents may run in, separated by spaces or commas. Exact names only, no wildcards."
        />
        {formError ? (
          <Banner tone="danger" title="Check the form" live="polite">
            {formError}
          </Banner>
        ) : null}
        {save.error ? <ApiErrorView error={save.error} /> : null}
      </div>
    </Dialog>
  );
}
