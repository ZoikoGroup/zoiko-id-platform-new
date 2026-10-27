"use client";

import type { SettingsArea, SettingsHistoryItem } from "@zoiko/api-client";
import { ApiError } from "@zoiko/api-client";
import { Banner, Button, Card, CopyId, DataTable, DateTime, EmptyState, type Column } from "@zoiko/ui";
import { History } from "lucide-react";

import { QueryView, useCellQuery } from "@/lib/cell";

export const SETTINGS_KEY = ["settings"];

/** A 409 CONFIGURATION_CONFLICT means someone else saved first: show it, never overwrite (UX-06). */
export function isConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === "CONFIGURATION_CONFLICT";
}

export function ConflictBanner({ onReload }: { onReload: () => void }) {
  return (
    <Banner tone="warn" title="This setting changed while you were editing" live="assertive">
      <p>Someone else saved a newer version. Your changes were not applied. Reload to see the current value, then make your change again.</p>
      <div className="mt-2">
        <Button size="sm" variant="secondary" onPress={onReload}>
          Reload current value
        </Button>
      </div>
    </Banner>
  );
}

/** Who changed an area and when, newest first. Values are closed vocabularies with no personal data. */
export function SettingsHistory({ area }: { area: SettingsArea }) {
  const history = useCellQuery([...SETTINGS_KEY, "history", area], (api) => api.settings.history(area));
  const columns: Column<SettingsHistoryItem>[] = [
    { key: "rev", header: "Revision", pinned: true, render: (h) => <span className="tabular">{h.revision}</span>, sortValue: (h) => h.revision },
    { key: "when", header: "Changed", render: (h) => (h.created_at ? <DateTime value={h.created_at} /> : "—") },
    { key: "by", header: "By", render: (h) => (h.created_by ? <CopyId value={h.created_by} label="principal ID" /> : "—") },
    {
      key: "value",
      header: "Value",
      render: (h) => <code className="block max-w-96 truncate font-mono text-xs">{JSON.stringify(h.value)}</code>,
    },
  ];
  return (
    <Card padded={false} title="Revision history" description="Every change is kept; a newer revision replaces an older one, nothing is overwritten.">
      <QueryView query={history}>
        {(d) => (
          <DataTable
            caption={`${area.toLowerCase()} settings history`}
            columns={columns}
            rows={d.items}
            rowKey={(h) => String(h.revision)}
            empty={<EmptyState icon={History} title="Never changed">The platform defaults apply.</EmptyState>}
          />
        )}
      </QueryView>
    </Card>
  );
}
