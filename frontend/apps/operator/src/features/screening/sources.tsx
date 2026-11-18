"use client";

import type { ScreeningRelease, ScreeningSource } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, PageHeader, type Column } from "@zoiko/ui";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { Mono, RegError } from "../regulatory/common";

import { actionsFor, approvalCounts, codeText, flagsToAcknowledge, FRESHNESS, shortDigest, STATE } from "./source-logic";

const KEY = ["screening", "sources"] as const;

function Comparison({ releaseId }: { releaseId: string }) {
  const q = useCellQuery([...KEY, "comparison", releaseId], (api) => api.regulatory.screeningComparison(releaseId));
  return (
    <QueryView query={q}>
      {(d) => (
        <div className="space-y-1 text-sm">
          <p>
            Against the release in use: <strong>{d.added}</strong> added, <strong>{d.removed}</strong> removed, <strong>{d.changed}</strong> changed.
            {!d.against_active ? " There is no release in use yet, so every entry is new." : ""}
          </p>
          {d.removed ? <p className="text-ink-muted">Removed entries are delistings: each case that relied on one is looked at again as a new version. First references: {d.removed_refs.slice(0, 8).join(", ")}.</p> : null}
        </div>
      )}
    </QueryView>
  );
}

function ReleaseRow({ r, onChanged }: { r: ScreeningRelease; onChanged: () => void }) {
  const [acknowledged, setAcknowledged] = useState<string[]>([]);
  const [showDiff, setShowDiff] = useState(false);
  const flags = flagsToAcknowledge(r);
  const counts = approvalCounts(r);
  const can = actionsFor(r);
  const refresh = { invalidate: [KEY as never], onSuccess: onChanged };
  const approve = useCellMutation((api, ack: string[]) => api.regulatory.screeningApprove(r.release_id, ack), { ...refresh, success: "Approval recorded" });
  const publish = useCellMutation<void, unknown>((api) => api.regulatory.screeningPublish(r.release_id), { ...refresh, success: "Now the corpus screening reads" });
  const approveBack = useCellMutation<void, unknown>((api) => api.regulatory.screeningApproveRollback(r.release_id), { ...refresh, success: "Rollback approval recorded" });
  const rollback = useCellMutation<void, unknown>((api) => api.regulatory.screeningRollback(r.release_id), { ...refresh, success: "Restored as the corpus screening reads" });
  const allAcknowledged = flags.every((f) => acknowledged.includes(f));
  return (
    <Card
      title={`${shortDigest(r.receipt.sha256)} · ${r.receipt.item_count} entries`}
      description={`Fetched ${new Date(r.receipt.fetched_at).toLocaleString()}. Staged by a person who cannot approve or publish it.`}
    >
      <div className="space-y-3 text-sm">
        <p>
          <Badge tone={STATE[r.state].tone}>{STATE[r.state].text}</Badge>{" "}
          {r.state === "VALIDATED" ? `${counts.publish} of 2 approvals.` : null}
          {r.state === "SUPERSEDED" ? `${counts.rollback} of 2 rollback approvals.` : null}
        </p>
        {r.blocked.length ? (
          <Banner tone="danger" title="Quarantined: this file can never be released">
            <ul className="list-disc pl-5">{r.blocked.map((c) => <li key={c}>{codeText(c)}</li>)}</ul>
          </Banner>
        ) : null}
        {r.flags.length ? (
          <div>
            <p className="font-medium text-ink">Anomalies each approver must acknowledge</p>
            <ul className="space-y-1">
              {flags.map((f) => (
                <li key={f}>
                  <label className="flex items-start gap-2">
                    <input type="checkbox" checked={acknowledged.includes(f)} disabled={!can.approve} onChange={() => setAcknowledged((a) => (a.includes(f) ? a.filter((x) => x !== f) : [...a, f]))} />
                    <span>{codeText(f)}</span>
                  </label>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {r.state === "VALIDATED" && r.publishable.length ? (
          <ul className="list-disc pl-5 text-ink-muted">{r.publishable.map((c) => <li key={c}>{codeText(c)}</li>)}</ul>
        ) : null}
        {r.state !== "QUARANTINED" ? (
          <>
            <Button variant="secondary" onPress={() => setShowDiff((v) => !v)}>{showDiff ? "Hide changes" : "Compare with the release in use"}</Button>
            {showDiff ? <Comparison releaseId={r.release_id} /> : null}
          </>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {can.approve ? <Button variant="secondary" pending={approve.isPending} isDisabled={!allAcknowledged} onPress={() => approve.mutate(acknowledged)}>Approve</Button> : null}
          {can.approve ? <Button pending={publish.isPending} isDisabled={!can.publish} onPress={() => publish.mutate()}>Publish</Button> : null}
          {can.rollback ? <Button variant="secondary" pending={approveBack.isPending} onPress={() => approveBack.mutate()}>Approve rolling back to this</Button> : null}
          {can.rollback ? <Button pending={rollback.isPending} isDisabled={counts.rollback < 2} onPress={() => rollback.mutate()}>Roll back to this</Button> : null}
        </div>
        {[approve, publish, approveBack, rollback].map((m, i) => (m.error ? <RegError key={i} error={m.error} /> : null))}
      </div>
    </Card>
  );
}

function Releases({ source }: { source: ScreeningSource }) {
  const q = useCellQuery([...KEY, source.source_id], (api) => api.regulatory.screeningReleases(source.source_id));
  const fetch = useCellMutation<void, unknown>((api) => api.regulatory.screeningFetch(source.source_id), { invalidate: [KEY as never], success: "Fetched and staged: inactive until two others approve" });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button pending={fetch.isPending} isDisabled={!source.address_on_record} onPress={() => fetch.mutate()}>Fetch and stage</Button>
        {!source.address_on_record ? <span className="text-ink-muted">No verified download address is on record, so nothing can be fetched. A file cannot be uploaded by hand.</span> : null}
      </div>
      {fetch.error ? <RegError error={fetch.error} /> : null}
      <QueryView query={q}>
        {(d) => (d.items.length ? <div className="space-y-3">{d.items.map((r) => <ReleaseRow key={r.release_id} r={r} onChanged={() => void q.refetch()} />)}</div> : <p className="text-ink-muted">No release has been staged for this source.</p>)}
      </QueryView>
    </div>
  );
}

export function ScreeningSourcesPage() {
  const list = useCellQuery([...KEY], (api) => api.regulatory.screeningSources());
  const [open, setOpen] = useState<ScreeningSource | null>(null);
  const columns: Column<ScreeningSource>[] = [
    { key: "s", header: "Source", pinned: true, render: (r) => <Mono>{r.source_id}</Mono> },
    { key: "f", header: "Corpus in use", render: (r) => <Badge tone={FRESHNESS[r.freshness].tone}>{FRESHNESS[r.freshness].text}</Badge> },
    { key: "v", header: "Last released", render: (r) => (r.last_validated_at ? <DateTime value={r.last_validated_at} /> : "never") },
    { key: "a", header: "Waiting", render: (r) => (r.awaiting_approval ? `${r.awaiting_approval} awaiting approval` : "none") },
    { key: "q", header: "Quarantined", render: (r) => r.quarantined },
    { key: "o", header: "", render: (r) => <Button variant="secondary" onPress={() => setOpen(r)}>Open</Button> },
  ];
  return (
    <>
      <PageHeader
        title="Sanctions sources"
        description="The official lists screening reads. A file is fetched from the publisher's recorded address, checked, and kept inactive until two people other than the one who staged it approve it. A list that is stale or has never been released makes screening unavailable, never clear."
        breadcrumbs={[{ label: "Screening" }, { label: "Sanctions sources" }]}
      />
      <div className="space-y-4">
        <QueryView query={list}>
          {(d) => <DataTable<ScreeningSource> caption="Official sources" rows={d.items} rowKey={(r) => r.source_id} empty="No sources." columns={columns} />}
        </QueryView>
        {open ? (
          <Card title={open.source_id} description={`Freshness follows the release, not the last attempt to fetch. A corpus older than ${open.max_age_hours} hours counts as stale.`}>
            <Releases source={open} />
          </Card>
        ) : null}
      </div>
    </>
  );
}
