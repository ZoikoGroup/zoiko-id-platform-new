"use client";

import { Banner, Button, Card, Checkbox, JsonView, Select, TextField } from "@zoiko/ui";
import { useState, type FormEvent } from "react";

import { ApiErrorView, useCellMutation } from "@/lib/op";

import { CapabilityHint } from "../recovery-shared";
import { buildRequest, type Action } from "./actions";

/** One operator write, rendered from its catalogue entry. The cell authorizes and validates it again; this
 * form only gathers values, asks for a confirmation sentence where the action is hard to undo, and shows
 * exactly what the cell answered. */
export function ActionForm({ action, onDone }: { action: Action; onDone?: () => void }) {
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [confirmText, setConfirmText] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const run = useCellMutation(
    (api, v: { path: string; body: Record<string, unknown> }) => api.commercial.call(action.method, v.path, v.body),
    { success: `${action.title}: done`, onSuccess: () => onDone?.() },
  );

  const needsConfirm = Boolean(action.confirm);
  const confirmed = !needsConfirm || confirmText.trim().toLowerCase() === action.confirm;

  function submit(e: FormEvent) {
    e.preventDefault();
    setLocalError(null);
    try {
      run.mutate(buildRequest(action, values));
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "The form is not valid.");
    }
  }

  const set = (key: string, v: string | boolean) => setValues((s) => ({ ...s, [key]: v }));

  return (
    <Card title={action.title} description={action.description}>
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2" aria-label={action.title}>
        {action.fields.map((f) => {
          const kind = f.kind ?? "text";
          if (kind === "boolean") {
            return (
              <div key={f.key} className="flex items-end pb-2">
                <Checkbox isSelected={values[f.key] === true} onChange={(v) => set(f.key, v)}>
                  {f.label}
                </Checkbox>
              </div>
            );
          }
          if (kind === "select") {
            return (
              <Select
                key={f.key}
                label={f.label}
                options={f.options ?? []}
                value={typeof values[f.key] === "string" ? (values[f.key] as string) : null}
                onChange={(v) => set(f.key, v)}
                isRequired={f.required}
              />
            );
          }
          return (
            <TextField
              key={f.key}
              label={f.label}
              value={typeof values[f.key] === "string" ? (values[f.key] as string) : ""}
              onChange={(v) => set(f.key, v)}
              isRequired={f.required}
              placeholder={f.placeholder}
              description={f.help}
              multiline={kind === "json"}
              rows={kind === "json" ? 5 : undefined}
              mono={kind === "json" || f.key.endsWith("_id") || f.key.endsWith("digest")}
              type={kind === "datetime" ? "datetime-local" : "text"}
              inputMode={kind === "integer" ? "numeric" : kind === "decimal" ? "decimal" : undefined}
              className={kind === "json" ? "sm:col-span-2" : undefined}
            />
          );
        })}
        {needsConfirm ? (
          <TextField
            label={`Type “${action.confirm}” to confirm`}
            value={confirmText}
            onChange={setConfirmText}
            className="sm:col-span-2"
            autoComplete="off"
          />
        ) : null}
        <div className="flex items-center gap-3 sm:col-span-2">
          <Button type="submit" isDisabled={run.isPending || !confirmed}>
            {run.isPending ? "Working…" : action.title}
          </Button>
          <span className="text-xs text-ink-subtle">
            Needs <span className="font-mono">{action.capability}</span>
          </span>
        </div>
      </form>
      <div aria-live="polite" className="mt-3 space-y-3">
        {localError ? <Banner tone="danger">{localError}</Banner> : null}
        {run.isError ? (
          <>
            <CapabilityHint error={run.error} capability={action.capability} />
            <ApiErrorView error={run.error} title={`${action.title} was not completed`} onRetry={() => run.reset()} />
          </>
        ) : null}
        {run.isSuccess ? <JsonView value={run.data} label={`${action.title} result`} /> : null}
      </div>
    </Card>
  );
}
