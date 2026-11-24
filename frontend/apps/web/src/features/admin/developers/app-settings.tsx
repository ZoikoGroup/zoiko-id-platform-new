"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { VerifyApiApplication } from "@zoiko/api-client";
import { Button, Checkbox, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation } from "@/lib/cell";

import { draftProblem, THEMES, toDraft, toSettings, WORKFLOWS, workflowLabel, type SettingsDraft } from "./settings-logic";

/** What this application may start, and how its verification page looks to your customers (CR-156). The page keeps its Zoiko iD design: you choose the name your customers see, one of the approved
 * colours, a help link and a language, and the addresses the page may send customers back to. */
export function AppSettings({ app }: { app: VerifyApiApplication }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<SettingsDraft>(() => toDraft(app));
  const set = (p: Partial<SettingsDraft>) => setDraft((d) => ({ ...d, ...p }));
  const problem = draftProblem(draft);
  const retired = app.status === "RETIRED";
  const save = useCellMutation((api) => api.verifyApi.updateSettings(app.application_id, toSettings(draft)), {
    success: "Settings saved",
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin", "developers", "verify-api"] }),
  });
  return (
    <section aria-label="Workflows and hosted page" className="space-y-4">
      <h3 className="text-sm font-semibold text-ink">Workflows and customer page</h3>
      <fieldset className="space-y-1.5" disabled={retired}>
        <legend className="text-sm font-medium text-ink">Workflows this application may start</legend>
        {WORKFLOWS.map((w) => (
          <Checkbox key={w.id} isSelected={draft.workflows.includes(w.id)} onChange={(on) => set({ workflows: on ? [...draft.workflows, w.id] : draft.workflows.filter((x) => x !== w.id) })}>
            <span className="font-medium">{w.label}</span> <span className="text-xs text-ink-muted">{w.help}</span>
          </Checkbox>
        ))}
      </fieldset>
      <Select
        label="Used when a request names none"
        options={[{ id: "", label: "Standard (ID document and selfie, if allowed)" }, ...draft.workflows.map((id) => ({ id, label: workflowLabel(id) }))]}
        value={draft.defaultWorkflow}
        onChange={(v) => set({ defaultWorkflow: v })}
        isDisabled={retired}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Name your customers see" description="Shown instead of the application name. Leave blank to use the application name." value={draft.displayName} onChange={(v) => set({ displayName: v })} isDisabled={retired} />
        <TextField label="Help link" description="An https address customers can open if they need help." value={draft.supportUrl} onChange={(v) => set({ supportUrl: v })} isDisabled={retired} />
        <TextField label="Language" description="For example en or en-GB. Leave blank to use each request's language." value={draft.locale} onChange={(v) => set({ locale: v })} isDisabled={retired} />
      </div>
      <fieldset className="space-y-1.5" disabled={retired}>
        <legend className="text-sm font-medium text-ink">Colour of the verification page</legend>
        <div className="flex flex-wrap gap-2">
          {[{ id: "", label: "Standard", swatch: "bg-brand-600" }, ...THEMES].map((t) => (
            <button
              key={t.id}
              type="button"
              aria-pressed={draft.theme === t.id}
              onClick={() => set({ theme: t.id })}
              className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm ${draft.theme === t.id ? "border-brand-600 ring-2 ring-brand-500" : "border-border"}`}
            >
              <span aria-hidden className={`size-3 rounded-full ${t.swatch}`} />
              {t.label}
            </button>
          ))}
        </div>
      </fieldset>
      <TextField
        label="Addresses customers may be sent back to"
        description="One address per line, for example https://app.example.com. A request can only name one of these."
        multiline
        rows={3}
        value={draft.origins}
        onChange={(v) => set({ origins: v })}
        isDisabled={retired}
      />
      {problem ? <p className="text-xs text-ink-muted">{problem}</p> : null}
      <Button size="sm" isDisabled={retired || problem !== null || save.isPending} pending={save.isPending} onPress={() => save.mutate(undefined)}>
        Save settings
      </Button>
      {save.error ? <ApiErrorView error={save.error} title="The settings were not saved" onRetry={() => save.reset()} /> : null}
    </section>
  );
}
