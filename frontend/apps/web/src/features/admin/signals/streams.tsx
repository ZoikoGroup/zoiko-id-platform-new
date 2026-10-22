"use client";

import type { SsfStream } from "@zoiko/api-client";
import { Badge, Button, Card, CopyId, DataTable, Dialog, EmptyState, StatusBadge, type Column } from "@zoiko/ui";
import { Plus, RadioTower } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { CreateStreamDialog } from "./create-stream-dialog";
import { STREAMS_KEY } from "./shared";
import { StreamDetailPanel } from "./stream-detail";

export function StreamsSection() {
  const streams = useCellQuery(STREAMS_KEY, (api) => api.signals.streams());
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<SsfStream | null>(null);
  const [toggling, setToggling] = useState<SsfStream | null>(null);

  const toggle = useCellMutation(
    (api, s: SsfStream) => (s.status === "ACTIVE" ? api.signals.disableStream(s.stream_id) : api.signals.enableStream(s.stream_id)),
    {
      invalidate: [STREAMS_KEY],
      success: (r) => (r.status === "ACTIVE" ? "Stream enabled" : "Stream quarantined"),
      onSuccess: (r) => {
        setToggling(null);
        setSelected((cur) => (cur && cur.stream_id === r.stream_id ? { ...cur, status: r.status } : cur));
      },
    },
  );

  const columns: Column<SsfStream>[] = [
    {
      key: "direction",
      header: "Stream",
      pinned: true,
      render: (s) => (
        <span className="inline-flex flex-col">
          <span>{s.direction === "RECEIVER" ? "Receiver" : "Transmitter"}</span>
          <span className="font-mono text-2xs text-ink-subtle">{s.stream_id.slice(0, 8)}…</span>
        </span>
      ),
      sortValue: (s) => s.direction,
    },
    { key: "status", header: "Status", render: (s) => <StatusBadge state={s.status} kind="Stream status" />, sortValue: (s) => s.status },
    {
      key: "peer",
      header: "Issuer / audience",
      render: (s) => (
        <span className="inline-flex max-w-72 flex-col font-mono text-xs">
          <span className="truncate" title={s.issuer ?? ""}>{s.issuer ?? "—"}</span>
          <span className="truncate text-ink-subtle" title={s.audience}>{s.audience}</span>
        </span>
      ),
    },
    { key: "events", header: "Events", render: (s) => <span className="tabular">{s.events.length}</span>, sortValue: (s) => s.events.length },
    { key: "trust", header: "Source trust", render: (s) => <Badge tone={s.source_trust === "AUTHORITATIVE" ? "warn" : "neutral"}>{s.source_trust}</Badge> },
    { key: "delivery", header: "Delivery", render: (s) => s.delivery_method, defaultHidden: true },
    { key: "id", header: "Stream ID", render: (s) => <CopyId value={s.stream_id} label="stream ID" />, defaultHidden: true },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (s) =>
        s.status === "ACTIVE" ? (
          <Button size="xs" variant="danger-outline" onPress={() => setToggling(s)}>
            Disable
          </Button>
        ) : (
          <Button size="xs" variant="secondary" onPress={() => setToggling(s)}>
            Enable
          </Button>
        ),
    },
  ];

  const disabling = toggling?.status === "ACTIVE";
  const cancelToggle = () => {
    setToggling(null);
    toggle.reset();
  };

  return (
    <>
      <Card
        padded={false}
        title="Shared Signals streams"
        description="Receivers accept pushed security events from trusted transmitters; transmitters publish this cell's events to receivers."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            Create stream
          </Button>
        }
      >
        <QueryView query={streams}>
          {(data) => (
            <DataTable
              caption="SSF streams"
              columns={columns}
              rows={data.items}
              rowKey={(s) => s.stream_id}
              onRowAction={setSelected}
              selectedKey={selected?.stream_id ?? null}
              empty={
                <EmptyState icon={RadioTower} title="No streams yet" action={<Button size="sm" onPress={() => setCreating(true)}>Create stream</Button>}>
                  Connect an external transmitter or publish this cell&apos;s events to a receiver.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>

      <CreateStreamDialog isOpen={creating} onClose={() => setCreating(false)} />
      <StreamDetailPanel stream={selected} onClose={() => setSelected(null)} onToggle={setToggling} />

      <Dialog
        isOpen={toggling !== null}
        onOpenChange={(o) => !o && !toggle.isPending && cancelToggle()}
        title={disabling ? "Disable this stream?" : "Enable this stream?"}
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={toggle.isPending} onPress={cancelToggle}>
              Cancel
            </Button>
            <Button variant={disabling ? "danger" : "primary"} pending={toggle.isPending} onPress={() => toggling && toggle.mutate(toggling)}>
              {disabling ? "Disable stream" : "Enable stream"}
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          {disabling ? (
            <p>
              The stream is quarantined at once: pushed events are refused and queued events stop being delivered. You can enable it
              again later.
            </p>
          ) : (
            <p>Events start flowing on this stream as soon as it is enabled. Check the pinned issuer, keys and source trust first.</p>
          )}
          {toggle.error ? <ApiErrorView error={toggle.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
