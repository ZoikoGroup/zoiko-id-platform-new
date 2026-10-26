"use client";

import type { WebhookDelivery } from "@zoiko/api-client";
import { Button, Card, CopyId, DataTable, EmptyState, Select, StatusBadge, type Column } from "@zoiko/ui";
import { Inbox } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { DELIVERIES_KEY, shortEvent } from "./shared";

const STATUSES = [
  { id: "", label: "All statuses" },
  { id: "QUEUED", label: "Queued" },
  { id: "RETRY_SCHEDULED", label: "Retry scheduled" },
  { id: "DELIVERED", label: "Delivered" },
  { id: "FAILED", label: "Failed" },
  { id: "EXHAUSTED", label: "Exhausted" },
];

export function DeliveriesSection() {
  const [status, setStatus] = useState("");
  const deliveries = useCellQuery([...DELIVERIES_KEY, status], (api) => api.webhooks.deliveries(status ? { status } : {}), {
    refetchInterval: 15000,
  });
  const replay = useCellMutation((api, id: string) => api.webhooks.replay(id), {
    invalidate: [DELIVERIES_KEY],
    success: "Replay queued",
  });

  const columns: Column<WebhookDelivery>[] = [
    {
      key: "event",
      header: "Event",
      pinned: true,
      render: (d) => (
        <span className="inline-flex flex-col">
          <span className="font-mono text-xs">{shortEvent(d.event_type)}</span>
          <span className="font-mono text-2xs text-ink-subtle">{d.delivery_id.slice(0, 8)}…</span>
        </span>
      ),
      sortValue: (d) => d.event_type,
    },
    { key: "status", header: "Status", render: (d) => <StatusBadge state={d.status} kind="Delivery status" />, sortValue: (d) => d.status },
    { key: "attempts", header: "Attempts", render: (d) => <span className="tabular">{d.attempts}</span>, sortValue: (d) => d.attempts },
    {
      key: "result",
      header: "Last result",
      render: (d) => (d.last_status_code ? `HTTP ${d.last_status_code}` : (d.last_error ?? "—")),
    },
    {
      key: "next",
      header: "Next attempt",
      render: (d) => (d.next_attempt_at ? new Date(d.next_attempt_at).toLocaleString() : "—"),
      defaultHidden: true,
    },
    {
      key: "created",
      header: "Queued",
      render: (d) => (d.created_at ? new Date(d.created_at).toLocaleString() : "—"),
      sortValue: (d) => d.created_at ?? "",
    },
    { key: "event_id", header: "Event ID", render: (d) => <CopyId value={d.event_id} label="event ID" />, defaultHidden: true },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (d) =>
        d.status === "FAILED" || d.status === "EXHAUSTED" ? (
          <Button size="xs" variant="secondary" pending={replay.isPending} onPress={() => replay.mutate(d.delivery_id)}>
            Replay
          </Button>
        ) : null,
    },
  ];

  return (
    <Card
      padded={false}
      title="Delivery log"
      description="The latest 200 deliveries. Failed and exhausted deliveries can be replayed with the same event ID."
      actions={<Select label="Status" options={STATUSES} value={status} onChange={setStatus} />}
    >
      {replay.error ? <ApiErrorView error={replay.error} /> : null}
      <QueryView query={deliveries}>
        {(data) => (
          <DataTable
            caption="Webhook deliveries"
            columns={columns}
            rows={data.items}
            rowKey={(d) => d.delivery_id}
            empty={
              <EmptyState icon={Inbox} title="No deliveries yet">
                Deliveries appear here when an event matches an active endpoint&apos;s subscription.
              </EmptyState>
            }
          />
        )}
      </QueryView>
    </Card>
  );
}
