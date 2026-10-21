"use client";

import type { AuditEvent, PartitionReport } from "@zoiko/api-client";
import { Banner, Button, Card, CopyId, DataTable, DateTime, EmptyState, EvidenceChip, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { ShieldCheck, History } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

export function AuditSection() {
  const [eventType, setEventType] = useState("");
  const [filter, setFilter] = useState("");
  const events = useCellQuery(["admin", "evidence", "audit", filter], (api) => api.evidence.auditEvents({ event_type: filter || undefined, limit: 100 }));
  const [report, setReport] = useState<PartitionReport | null>(null);
  const verify = useCellMutation((api) => api.evidence.verifyPartition(), { onSuccess: setReport });

  const columns: Column<AuditEvent>[] = [
    { key: "event", header: "Event", pinned: true, render: (e) => <span className="font-mono text-xs">{e.event_type}</span>, sortValue: (e) => e.event_type },
    { key: "outcome", header: "Outcome", render: (e) => <StatusBadge state={e.outcome} kind="Audit outcome" /> },
    { key: "class", header: "Evidence", render: (e) => <EvidenceChip evidenceClass={e.evidence_class} /> },
    { key: "actor", header: "Actor", render: (e) => <span className="text-xs">{e.actor_type}</span> },
    {
      key: "target",
      header: "Target",
      render: (e) => (
        <span className="inline-flex flex-col text-xs">
          <span>{e.target_type}</span>
          <span className="font-mono text-2xs text-ink-subtle">{e.target_ref.slice(0, 24)}</span>
        </span>
      ),
    },
    { key: "at", header: "Occurred", render: (e) => <DateTime value={e.occurred_at} />, sortValue: (e) => e.occurred_at },
    { key: "hash", header: "Record hash", render: (e) => <CopyId value={e.record_hash} label="record hash" />, defaultHidden: true },
  ];

  return (
    <div className="space-y-5">
      <Card
        title="Ledger integrity"
        description="Recompute this tenant's whole hash chain and every signed Merkle checkpoint. A failure is raised as a security incident."
        actions={
          <Button size="sm" icon={<ShieldCheck aria-hidden className="size-4" />} pending={verify.isPending} onPress={() => verify.mutate(undefined)}>
            Verify the ledger
          </Button>
        }
      >
        {verify.error ? <ApiErrorView error={verify.error} /> : null}
        {report ? (
          <Banner tone={report.ok ? "ok" : "danger"} title={report.ok ? "The ledger verifies end to end" : `Integrity failure from sequence ${report.first_broken_sequence}`}>
            {report.entries} entries, {report.checkpoints} checkpoints, sealed through {report.sealed_through}
            {report.unsealed ? ` (${report.unsealed} awaiting the next checkpoint)` : ""}.
          </Banner>
        ) : (
          <p className="text-sm text-ink-muted">Run a verification to see the current integrity state.</p>
        )}
      </Card>
      <Card
        padded={false}
        title="Protected audit records"
        description="Administrative and security changes (§7.1): operator access, privacy, residency, workload identity and more."
        actions={
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setFilter(eventType.trim());
            }}
          >
            <TextField label="Event type" value={eventType} onChange={setEventType} placeholder="privacy.consent.given" mono />
            <Button size="sm" variant="secondary" type="submit">
              Filter
            </Button>
          </form>
        }
      >
        <QueryView query={events}>
          {(data) => (
            <DataTable
              caption="Audit records"
              columns={columns}
              rows={data.items}
              rowKey={(e) => e.audit_id}
              empty={<EmptyState icon={History} title="No audit records">Protected changes in this tenant appear here.</EmptyState>}
            />
          )}
        </QueryView>
      </Card>
    </div>
  );
}
