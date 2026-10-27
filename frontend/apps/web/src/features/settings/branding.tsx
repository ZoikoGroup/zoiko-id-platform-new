"use client";

import type { BrandAccent, BrandingSettings } from "@zoiko/api-client";
import { Banner, Button, Card, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { ConflictBanner, isConflict, SETTINGS_KEY, SettingsHistory } from "./shared";

const ACCENTS: { id: BrandAccent; label: string; swatch: string }[] = [
  { id: "blue", label: "Blue", swatch: "#2563eb" },
  { id: "teal", label: "Teal", swatch: "#0d9488" },
  { id: "green", label: "Green", swatch: "#16a34a" },
  { id: "violet", label: "Violet", swatch: "#7c3aed" },
  { id: "orange", label: "Orange", swatch: "#ea580c" },
  { id: "slate", label: "Slate", swatch: "#475569" },
];

export function BrandingSettingsPanel() {
  const all = useCellQuery(SETTINGS_KEY, (api) => api.settings.all());
  return (
    <div className="space-y-4">
      <QueryView query={all}>
        {(data) => <Form key={data.areas.BRANDING.revision} revision={data.areas.BRANDING.revision} value={data.areas.BRANDING.value} reload={() => void all.refetch()} />}
      </QueryView>
      <SettingsHistory area="BRANDING" />
    </div>
  );
}

function Form({ revision, value, reload }: { revision: number; value: BrandingSettings; reload: () => void }) {
  const [state, setState] = useState<BrandingSettings>(value);
  const save = useCellMutation((api, v: BrandingSettings) => api.settings.put<BrandingSettings>("BRANDING", revision, v), {
    invalidate: [SETTINGS_KEY],
    success: "Branding saved. It appears on your sign-in page.",
  });
  const dirty = JSON.stringify(state) !== JSON.stringify(value);
  const swatch = ACCENTS.find((a) => a.id === state.accent)?.swatch ?? "#2563eb";

  return (
    <Card title="Sign-in page" description={`Revision ${revision}. How your company appears to people signing in. Presentation only: it never changes security.`}>
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-4">
          <TextField label="Display name" value={state.display_name} onChange={(v) => setState({ ...state, display_name: v })} description="Leave empty to use your company name. Names that imply the platform or its staff are refused." />
          <Select label="Accent colour" options={ACCENTS} value={state.accent} onChange={(v) => setState({ ...state, accent: v as BrandAccent })} description="A fixed set of accessible colours." />
          <TextField label="Support link" mono value={state.support_url} onChange={(v) => setState({ ...state, support_url: v })} placeholder="https://help.example.com" description="Where people get help. https only." />
          {isConflict(save.error) ? <ConflictBanner onReload={() => { save.reset(); reload(); }} /> : save.error ? <ApiErrorView error={save.error} /> : null}
          <Button isDisabled={!dirty} pending={save.isPending} onPress={() => save.mutate(state)}>
            Save branding
          </Button>
        </div>
        <div aria-label="Preview of your sign-in page" className="rounded-lg border border-border p-5">
          <p className="mb-3 text-2xs uppercase tracking-wide text-ink-subtle">Preview</p>
          <div className="space-y-3">
            <div className="h-1.5 w-16 rounded-full" style={{ backgroundColor: swatch }} />
            <p className="text-lg font-semibold text-ink">Sign in to {state.display_name.trim() || "your company"}</p>
            <p className="text-sm text-ink-muted">Use your passkey to continue.</p>
            <span className="inline-block rounded-md px-3 py-1.5 text-sm font-medium text-white" style={{ backgroundColor: swatch }}>
              Continue with passkey
            </span>
            {state.support_url ? <p className="text-xs text-ink-muted">Need help? <span className="underline">{state.support_url}</span></p> : null}
          </div>
          <Banner tone="info" title="Always shown by the platform">
            Security text, the platform domain and passkey prompts cannot be restyled, so people can tell a real sign-in page.
          </Banner>
        </div>
      </div>
    </Card>
  );
}
