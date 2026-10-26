"use client";

import type { SsfStream } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, CopyId, KeyValues, NumberValue, SidePanel, StatusBadge, TextField } from "@zoiko/ui";
import { Link2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { isUuid } from "./shared";

const REFRESH_MS = 5000;

function Ms({ value }: { value: number | null }) {
  if (value === null) return <span className="text-ink-subtle">—</span>;
  return <span className="tabular">{value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`}</span>;
}

export function Propagation({ streamId }: { streamId: string }) {
  const q = useCellQuery(["admin", "signals", "streams", streamId, "propagation"], (api) => api.signals.propagation(streamId), {
    refetchInterval: REFRESH_MS,
  });
  return (
    <Card title="Propagation" description={`Commit to receiver acknowledgement. Refreshes every ${REFRESH_MS / 1000}s.`}>
      <QueryView query={q} loadingRows={3}>
        {(p) => {
          const breaches = p.slo_breaches;
          return (
            <div className="space-y-4">
              <KeyValues
                items={[
                  ["Acknowledged", <NumberValue key="a" value={p.acknowledged} />],
                  ["Pending", <NumberValue key="p" value={p.pending} />],
                  ["p50 latency", <Ms key="50" value={p.p50_ms} />],
                  ["p99 latency", <Ms key="99" value={p.p99_ms} />],
                  ["Max latency", <Ms key="max" value={p.max_ms} />],
                  ["Oldest pending", <Ms key="old" value={p.oldest_pending_ms} />],
                ]}
              />
              {p.remote_fail_closed === true ? (
                <Banner tone="danger" title="The remote side is told to fail closed">
                  {Number(p.overdue_restricting ?? 0)} restricting event{Number(p.overdue_restricting ?? 0) === 1 ? "" : "s"} not acknowledged within the
                  target. Until the receiver acknowledges, it must treat the affected subjects as restricted.
                </Banner>
              ) : null}
              {breaches > 0 ? (
                <Banner tone="warn" title={`${breaches} SLO breach${breaches === 1 ? "" : "es"}`}>
                  Acknowledged events that exceeded their risk-class propagation target.
                </Banner>
              ) : (
                <p className="text-xs text-ink-subtle">No acknowledged event has exceeded its propagation target.</p>
              )}
            </div>
          );
        }}
      </QueryView>
    </Card>
  );
}

function MapSubject({ streamId }: { streamId: string }) {
  const [opaque, setOpaque] = useState("");
  const [principal, setPrincipal] = useState("");
  const [invalid, setInvalid] = useState<string | undefined>();
  const map = useCellMutation((api, v: { opaque_id: string; principal_id: string }) => api.signals.mapSubject(streamId, v), {
    success: "Subject mapped",
    onSuccess: () => {
      setOpaque("");
      setPrincipal("");
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!isUuid(principal)) return setInvalid("Enter a principal UUID.");
    setInvalid(undefined);
    map.mutate({ opaque_id: opaque.trim(), principal_id: principal.trim() });
  };
  return (
    <Card title="Map an opaque subject" description="Binds an opaque subject identifier used on this stream to a principal.">
      <form onSubmit={submit} className="space-y-3">
        <TextField label="Opaque subject ID" isRequired value={opaque} onChange={setOpaque} mono />
        <TextField label="Principal ID" isRequired value={principal} onChange={setPrincipal} mono errorMessage={invalid} />
        {map.error ? <ApiErrorView error={map.error} /> : null}
        <div className="flex justify-end">
          <Button type="submit" variant="secondary" pending={map.isPending} isDisabled={!opaque.trim() || !principal.trim()} icon={<Link2 aria-hidden className="size-4" />}>
            Map subject
          </Button>
        </div>
      </form>
    </Card>
  );
}

export function StreamDetailPanel({
  stream,
  onClose,
  onToggle,
}: {
  stream: SsfStream | null;
  onClose: () => void;
  onToggle: (s: SsfStream) => void;
}) {
  const detail = useCellQuery(["admin", "signals", "streams", stream?.stream_id ?? "", "detail"], (api) => api.signals.stream(stream!.stream_id), {
    enabled: Boolean(stream),
  });
  const s = detail.data ?? stream;
  return (
    <SidePanel
      isOpen={stream !== null}
      onOpenChange={(o) => !o && onClose()}
      title={s ? `${s.direction === "RECEIVER" ? "Receiver" : "Transmitter"} stream` : "Stream"}
      subtitle={s ? <CopyId value={s.stream_id} label="stream ID" /> : null}
      footer={
        s ? (
          s.status === "ACTIVE" ? (
            <Button variant="danger-outline" onPress={() => onToggle(s)}>
              Disable (quarantine)
            </Button>
          ) : (
            <Button onPress={() => onToggle(s)}>Enable</Button>
          )
        ) : null
      }
    >
      {s ? (
        <div className="space-y-5">
          {detail.error ? <ApiErrorView error={detail.error} title="Could not refresh this stream" /> : null}
          <KeyValues
            columns={1}
            items={[
              ["Status", <StatusBadge key="st" state={s.status} kind="Stream status" />],
              ["Stream reference", <code key="ref" className="font-mono text-xs break-all">{s.stream_ref}</code>],
              ["Delivery", <span key="d">{s.delivery_method === "push" ? "Push (RFC 8935)" : "Poll (RFC 8936)"}</span>],
              [
                s.direction === "RECEIVER" ? "Push endpoint" : "Poll endpoint",
                <code key="ep" className="font-mono text-xs break-all">
                  {s.direction === "RECEIVER" ? "/ssf/events/push/" : "/ssf/poll/"}
                  {s.stream_ref}
                </code>,
              ],
              ["Issuer", s.issuer ? <code key="i" className="font-mono text-xs break-all">{s.issuer}</code> : null],
              ["Audience", <code key="a" className="font-mono text-xs break-all">{s.audience}</code>],
              ["Source trust", <Badge key="t" tone={s.source_trust === "AUTHORITATIVE" ? "warn" : "neutral"}>{s.source_trust}</Badge>],
              [
                "Events",
                <ul key="e" className="flex flex-wrap gap-1">
                  {s.events.map((e) => (
                    <li key={e}>
                      <Badge tone="neutral">{e}</Badge>
                    </li>
                  ))}
                </ul>,
              ],
              ["Subject formats", s.subject_formats.join(", ")],
              ["Metadata revision", <span key="r" className="tabular">{s.metadata_revision}</span>],
            ]}
          />
          <Propagation streamId={s.stream_id} />
          <MapSubject streamId={s.stream_id} />
        </div>
      ) : null}
    </SidePanel>
  );
}
