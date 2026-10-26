"use client";

import type { AlertSeverity, AlertState, PlatformAlert } from "@zoiko/api-client";
import { Badge, Button, Card, DataTable, EmptyState, PageHeader, Select, StatusBadge, type Column } from "@zoiko/ui";
import { BellRing } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { ageOf, ALERT_SUMMARY_KEY, ALERTS_KEY } from "./shared";

const SEVERITY_TONE: Record<AlertSeverity, "danger" | "warn" | "info" | "neutral"> = {
  CRITICAL: "danger",
  HIGH: "warn",
  MEDIUM: "info",
  INFO: "neutral",
};

const SEVERITIES = [
  { id: "", label: "All severities" },
  { id: "CRITICAL", label: "Critical" },
  { id: "HIGH", label: "High" },
  { id: "MEDIUM", label: "Medium" },
  { id: "INFO", label: "Info" },
];

const STATES = [
  { id: "", label: "Open and acknowledged" },
  { id: "OPEN", label: "Open (not yet seen)" },
  { id: "ACKNOWLEDGED", label: "Acknowledged" },
  { id: "RESOLVED", label: "Resolved" },
];

export function AlertsPage() {
  const [severity, setSeverity] = useState("");
  const [state, setState] = useState("");
  const alerts = useCellQuery([...ALERTS_KEY, state, severity], (api) =>
    api.alerts.list({
      state: (state || undefined) as AlertState | undefined,
      severity: (severity || undefined) as AlertSeverity | undefined,
    }),
  );
  const summary = useCellQuery(ALERT_SUMMARY_KEY, (api) => api.alerts.summary());

  const ack = useCellMutation((api, id: string) => api.alerts.acknowledge(id), {
    invalidate: [ALERTS_KEY],
    success: "Alert acknowledged. It stays open until it is resolved.",
  });
  const resolve = useCellMutation((api, id: string) => api.alerts.resolve(id), {
    invalidate: [ALERTS_KEY],
    success: "Alert resolved",
  });

  const columns: Column<PlatformAlert>[] = [
    {
      key: "severity",
      header: "Severity",
      pinned: true,
      render: (a) => <Badge tone={SEVERITY_TONE[a.severity]}>{a.severity}</Badge>,
      sortValue: (a) => ["CRITICAL", "HIGH", "MEDIUM", "INFO"].indexOf(a.severity),
    },
    {
      key: "title",
      header: "Alert",
      render: (a) => (
        <span className="inline-flex max-w-96 flex-col gap-0.5">
          <span className="font-medium">
            {a.title}
            {a.is_task ? (
              <Badge size="xs" tone="brand" className="ml-2">
                Task
              </Badge>
            ) : null}
          </span>
          <span className="text-xs text-ink-muted">{a.recommended_action}</span>
          <span className="font-mono text-2xs text-ink-subtle">{a.object_ref}</span>
        </span>
      ),
    },
    { key: "state", header: "State", render: (a) => <StatusBadge state={a.state} kind="Alert state" />, sortValue: (a) => a.state },
    {
      key: "seen",
      header: "Last seen",
      render: (a) => (
        <span className="inline-flex flex-col text-xs">
          <span>{ageOf(a.last_seen_at)}</span>
          {a.occurrences > 1 ? <span className="text-ink-subtle">{a.occurrences} occurrences</span> : null}
        </span>
      ),
      sortValue: (a) => a.last_seen_at,
    },
    {
      key: "handled",
      header: "Handled by",
      defaultHidden: true,
      render: (a) =>
        a.resolved_at
          ? `Resolved ${new Date(a.resolved_at).toLocaleString()}`
          : a.acknowledged_at
            ? `Acknowledged ${new Date(a.acknowledged_at).toLocaleString()}`
            : "—",
    },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (a) => (
        <span className="inline-flex gap-2">
          {a.href ? (
            <Link href={a.href} className="inline-flex items-center rounded-md px-2 py-1 text-xs font-medium text-brand-600 hover:underline">
              Open
            </Link>
          ) : null}
          {a.state === "OPEN" ? (
            <Button size="xs" variant="secondary" pending={ack.isPending} onPress={() => ack.mutate(a.alert_id)}>
              Acknowledge
            </Button>
          ) : null}
          {a.state !== "RESOLVED" ? (
            <Button size="xs" pending={resolve.isPending} onPress={() => resolve.mutate(a.alert_id)}>
              Resolve
            </Button>
          ) : null}
        </span>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Alerts & tasks"
        description="Things that need attention, most severe first. Acknowledging says you have seen an alert; only resolving closes it. Critical and high alerts are mandatory and cannot be muted."
      />
      <QueryView query={summary}>
        {(s) => (
          <p className="mb-4 text-sm text-ink-muted" role="status">
            {s.open} open · {s.unacknowledged} not yet acknowledged · {s.by_severity.CRITICAL} critical · {s.by_severity.HIGH} high
          </p>
        )}
      </QueryView>
      <Card
        padded={false}
        title="Inbox"
        actions={
          <span className="flex gap-3">
            <Select label="Severity" options={SEVERITIES} value={severity} onChange={setSeverity} />
            <Select label="State" options={STATES} value={state} onChange={setState} />
          </span>
        }
      >
        {ack.error ? <ApiErrorView error={ack.error} /> : null}
        {resolve.error ? <ApiErrorView error={resolve.error} /> : null}
        <QueryView query={alerts}>
          {(data) => (
            <DataTable
              caption="Alerts and tasks"
              columns={columns}
              rows={data.items}
              rowKey={(a) => a.alert_id}
              empty={
                <EmptyState icon={BellRing} title="Nothing needs your attention">
                  Alerts appear here when the platform detects a revoked authority, a failing integration, an expiring credential or similar.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>
    </>
  );
}
