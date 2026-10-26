"use client";

import type { WebhookEndpoint } from "@zoiko/api-client";
import { Badge, Button, Card, CopyId, DataTable, Dialog, EmptyState, StatusBadge, type Column } from "@zoiko/ui";
import { Plus, Webhook } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { CreateEndpointDialog } from "./create-endpoint-dialog";
import { DELIVERIES_KEY, ENDPOINTS_KEY, shortEvent } from "./shared";

type Change = "verify" | "pause" | "resume" | "retire";

const COPY: Record<Change, { title: string; body: string; cta: string; danger?: boolean }> = {
  verify: {
    title: "Verify this endpoint?",
    body: "Zoiko iD sends one signed challenge to the URL. The endpoint turns ACTIVE only if it answers 200 with the same challenge.",
    cta: "Send challenge",
  },
  pause: {
    title: "Pause deliveries?",
    body: "New events are still queued but nothing is sent until you resume the endpoint.",
    cta: "Pause endpoint",
  },
  resume: { title: "Resume deliveries?", body: "Queued events are sent again as soon as the endpoint is active.", cta: "Resume endpoint" },
  retire: {
    title: "Retire this endpoint?",
    body: "Retiring is permanent. Queued deliveries are never sent and the URL cannot be reactivated; register a new endpoint instead.",
    cta: "Retire endpoint",
    danger: true,
  },
};

export function EndpointsSection() {
  const endpoints = useCellQuery(ENDPOINTS_KEY, (api) => api.webhooks.endpoints());
  const [creating, setCreating] = useState(false);
  const [pending, setPending] = useState<{ endpoint: WebhookEndpoint; change: Change } | null>(null);

  const apply = useCellMutation(
    (api, v: { id: string; change: Change }) => {
      switch (v.change) {
        case "verify":
          return api.webhooks.verifyEndpoint(v.id);
        case "pause":
          return api.webhooks.pauseEndpoint(v.id);
        case "resume":
          return api.webhooks.resumeEndpoint(v.id);
        case "retire":
          return api.webhooks.retireEndpoint(v.id);
      }
    },
    {
      invalidate: [ENDPOINTS_KEY, DELIVERIES_KEY],
      success: (r) => `Endpoint is now ${r.status.toLowerCase().replace(/_/g, " ")}`,
      onSuccess: () => setPending(null),
    },
  );

  const cancel = () => {
    setPending(null);
    apply.reset();
  };

  const columns: Column<WebhookEndpoint>[] = [
    {
      key: "url",
      header: "Endpoint",
      pinned: true,
      render: (e) => (
        <span className="inline-flex max-w-96 flex-col">
          <span className="truncate font-mono text-xs" title={e.url}>
            {e.url}
          </span>
          <span className="font-mono text-2xs text-ink-subtle">{e.endpoint_id.slice(0, 8)}…</span>
        </span>
      ),
      sortValue: (e) => e.url,
    },
    { key: "status", header: "Status", render: (e) => <StatusBadge state={e.status} kind="Endpoint status" />, sortValue: (e) => e.status },
    {
      key: "events",
      header: "Events",
      render: (e) => (
        <span className="inline-flex max-w-72 flex-wrap gap-1">
          {e.event_types.slice(0, 2).map((t) => (
            <Badge key={t} size="xs">
              {shortEvent(t)}
            </Badge>
          ))}
          {e.event_types.length > 2 ? (
            <Badge size="xs" tone="info">
              +{e.event_types.length - 2}
            </Badge>
          ) : null}
        </span>
      ),
      sortValue: (e) => e.event_types.length,
    },
    { key: "inflight", header: "Max in flight", render: (e) => <span className="tabular">{e.max_inflight}</span>, defaultHidden: true },
    { key: "key", header: "Signing key", render: (e) => <CopyId value={e.signing_key_id} label="signing key ID" />, defaultHidden: true },
    { key: "id", header: "Endpoint ID", render: (e) => <CopyId value={e.endpoint_id} label="endpoint ID" />, defaultHidden: true },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (e) => (
        <span className="inline-flex gap-2">
          {e.status === "PENDING_VERIFICATION" ? (
            <Button size="xs" onPress={() => setPending({ endpoint: e, change: "verify" })}>
              Verify
            </Button>
          ) : null}
          {e.status === "ACTIVE" ? (
            <Button size="xs" variant="secondary" onPress={() => setPending({ endpoint: e, change: "pause" })}>
              Pause
            </Button>
          ) : null}
          {e.status === "PAUSED" ? (
            <Button size="xs" variant="secondary" onPress={() => setPending({ endpoint: e, change: "resume" })}>
              Resume
            </Button>
          ) : null}
          {e.status !== "RETIRED" ? (
            <Button size="xs" variant="danger-outline" onPress={() => setPending({ endpoint: e, change: "retire" })}>
              Retire
            </Button>
          ) : null}
        </span>
      ),
    },
  ];

  const copy = pending ? COPY[pending.change] : null;

  return (
    <>
      <Card
        padded={false}
        title="Webhook endpoints"
        description="HTTPS receivers for integration events. Every delivery is signed (RFC 9421), retried with backoff, and never follows redirects."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            Register endpoint
          </Button>
        }
      >
        <QueryView query={endpoints}>
          {(data) => (
            <DataTable
              caption="Webhook endpoints"
              columns={columns}
              rows={data.items}
              rowKey={(e) => e.endpoint_id}
              empty={
                <EmptyState
                  icon={Webhook}
                  title="No webhook endpoints yet"
                  action={
                    <Button size="sm" onPress={() => setCreating(true)}>
                      Register endpoint
                    </Button>
                  }
                >
                  Register an HTTPS URL to receive events such as mandate revocations or credential status changes.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>

      <CreateEndpointDialog isOpen={creating} onClose={() => setCreating(false)} />

      <Dialog
        isOpen={pending !== null}
        onOpenChange={(o) => !o && !apply.isPending && cancel()}
        title={copy?.title ?? ""}
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={apply.isPending} onPress={cancel}>
              Cancel
            </Button>
            <Button
              variant={copy?.danger ? "danger" : "primary"}
              pending={apply.isPending}
              onPress={() => pending && apply.mutate({ id: pending.endpoint.endpoint_id, change: pending.change })}
            >
              {copy?.cta}
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <p className="font-mono text-xs break-all">{pending?.endpoint.url}</p>
          <p>{copy?.body}</p>
          {apply.error ? <ApiErrorView error={apply.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
