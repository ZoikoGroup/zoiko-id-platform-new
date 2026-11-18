"use client";

import { ApiError, type TrustPackage } from "@zoiko/api-client";
import { AccessUnavailable, Badge, Banner, Button, Card, DataTable, DateTime, EmptyState, LoadingRows, PageHeader } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { anchorFingerprint, fingerprintsMatch, problemText, STATE, stepsFor, toBase64, TRUST_KEY, type Step } from "./logic";

function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 403 && !error.isStepUpRequired && !error.isApprovalRequired) {
    const text = problemText(String(error.code ?? ""));
    if (text !== String(error.code ?? "")) return <Banner tone="danger" title="Not allowed">{text}</Banner>;
    return <EmptyState title="Withheld for your role">Your role does not include this. Nothing was changed.</EmptyState>;
  }
  if (error instanceof ApiError && error.status === 404) return <AccessUnavailable correlationId={error.correlationId} />;
  if (error instanceof ApiError && (error.status === 409 || error.status === 422)) {
    return <Banner tone="danger" title="This could not be done">{problemText(String(error.code ?? ""))}</Banner>;
  }
  return <ApiErrorView error={error} />;
}

function ActiveBanner() {
  const active = useCellQuery([...TRUST_KEY, "active"], (api) => api.trustStore.active());
  if (!active.data) return active.isError ? <Failed error={active.error} /> : null;
  const a = active.data;
  if (!a.governed) {
    return (
      <Banner tone="warn" title="Nothing here is trusted yet">
        Chip checks use the certificates your deployment was configured with. They use this store only after a package has been staged, validated, approved and activated.
      </Banner>
    );
  }
  return a.package_id ? (
    <Banner tone="ok" title={`Package ${a.package_seq} is active`}>
      {a.certificates} country signing certificates. Manifest <code className="font-mono text-xs">{a.manifest_digest?.slice(0, 16)}</code>. This store is now the whole truth for chip checks.
    </Banner>
  ) : (
    <Banner tone="danger" title="This store trusts nothing">
      The active package was retired, so no chip can be certified until another package is activated.
    </Banner>
  );
}

function StageForm({ onDone }: { onDone: () => void }) {
  const [list, setList] = useState<Uint8Array | null>(null);
  const [anchor, setAnchor] = useState<Uint8Array | null>(null);
  const [computed, setComputed] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [uri, setUri] = useState("");
  const [note, setNote] = useState("");
  const stage = useCellMutation((api, body: Parameters<typeof api.trustStore.stage>[0]) => api.trustStore.stage(body), {
    invalidate: [TRUST_KEY],
    success: "Package staged. Nothing is trusted yet.",
    onSuccess: onDone,
  });

  async function pick(file: File | undefined, kind: "list" | "anchor") {
    if (!file) return;
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (kind === "list") setList(bytes);
    else {
      setAnchor(bytes);
      setComputed(await anchorFingerprint(bytes));
    }
  }

  const ready = Boolean(list && anchor && computed && fingerprintsMatch(typed, computed) && uri.trim().length >= 3);
  return (
    <Card title="Stage a country list" description="Importing is not trusting. You supply the master list, the anchor you confirmed with the country, and where you got them.">
      <div className="space-y-3 text-sm">
        <label className="block font-medium text-ink" htmlFor="ts-list">Master list file</label>
        <input id="ts-list" type="file" onChange={(e) => void pick(e.target.files?.[0], "list")} />
        <label className="block font-medium text-ink" htmlFor="ts-anchor">Anchor certificate (PEM or DER)</label>
        <input id="ts-anchor" type="file" accept=".pem,.crt,.cer" onChange={(e) => void pick(e.target.files?.[0], "anchor")} />
        {computed ? (
          <p>
            This anchor&apos;s SHA-256 fingerprint is <code className="break-all font-mono text-xs">{computed}</code>. Compare it with the country&apos;s own publication, then type it below.
          </p>
        ) : null}
        <label className="block font-medium text-ink" htmlFor="ts-fp">Type the fingerprint you confirmed</label>
        <input id="ts-fp" value={typed} onChange={(e) => setTyped(e.target.value)} className="w-full rounded-control border border-border bg-surface p-2 font-mono text-xs text-ink" />
        {computed && typed && !fingerprintsMatch(typed, computed) ? <p className="text-danger">That is not this anchor&apos;s fingerprint.</p> : null}
        <label className="block font-medium text-ink" htmlFor="ts-uri">Where the list was published (address)</label>
        <input id="ts-uri" value={uri} onChange={(e) => setUri(e.target.value)} className="w-full rounded-control border border-border bg-surface p-2 text-sm text-ink" />
        <label className="block font-medium text-ink" htmlFor="ts-note">Note (optional)</label>
        <input id="ts-note" value={note} onChange={(e) => setNote(e.target.value)} className="w-full rounded-control border border-border bg-surface p-2 text-sm text-ink" />
        <Button
          pending={stage.isPending}
          isDisabled={!ready}
          onPress={() => {
            if (!list || !anchor || !computed) return;
            stage.mutate({ master_list_base64: toBase64(list), anchors_base64: [toBase64(anchor)], confirmed_anchor_sha256: [computed], source_uri: uri.trim(), ...(note.trim() ? { source_note: note.trim() } : {}) });
          }}
        >
          Stage package
        </Button>
        {stage.error ? <Failed error={stage.error} /> : null}
      </div>
    </Card>
  );
}

function ReasonAction({ label, onSubmit, pending, error }: { label: string; onSubmit: (reason: string) => void; pending: boolean; error: unknown }) {
  const [reason, setReason] = useState("");
  const id = `ts-reason-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <div className="space-y-2 rounded-control border border-border p-3">
      <label className="block text-sm text-ink" htmlFor={id}>
        Reason for “{label}”
      </label>
      <textarea id={id} value={reason} onChange={(e) => setReason(e.target.value)} rows={2} className="w-full rounded-control border border-border bg-surface p-2 text-sm text-ink" />
      <Button variant="secondary" pending={pending} isDisabled={reason.trim().length < 3} onPress={() => onSubmit(reason.trim())}>
        {label}
      </Button>
      {error ? <Failed error={error} /> : null}
    </div>
  );
}

function PackageCard({ pkg, me }: { pkg: TrustPackage; me?: string }) {
  const s = STATE[pkg.state];
  const { steps, blockedBecauseYouStaged } = stepsFor(pkg, me);
  const common = { invalidate: [TRUST_KEY] };
  const validate = useCellMutation<void, TrustPackage>((api) => api.trustStore.validate(pkg.package_id), { ...common, success: "Validation finished" });
  const approve = useCellMutation<void, TrustPackage>((api) => api.trustStore.approve(pkg.package_id), { ...common, success: "Approved. It is not active yet." });
  const activate = useCellMutation<void, TrustPackage>((api) => api.trustStore.activate(pkg.package_id), { ...common, success: "Activated. Chip checks now use it." });
  const reject = useCellMutation((api, reason: string) => api.trustStore.reject(pkg.package_id, reason), { ...common, success: "Rejected" });
  const run: Record<Exclude<Step, "reject">, () => void> = { validate: () => validate.mutate(), approve: () => approve.mutate(), activate: () => activate.mutate() };
  const labels: Record<Exclude<Step, "reject">, string> = { validate: "Validate against the anchor", approve: "Approve", activate: "Activate" };
  return (
    <Card title={`Package ${pkg.seq}`} description={pkg.source_uri}>
      <div className="space-y-3 text-sm">
        <p>
          <Badge tone={s.tone}>{s.label}</Badge> {s.meaning}
        </p>
        <p className="text-ink-muted">
          Staged <DateTime value={pkg.staged_at} />. List <code className="font-mono text-xs">{pkg.package_sha256.slice(0, 12)}</code>, {pkg.certificate_count} certificates
          {pkg.manifest_digest ? (
            <>
              , manifest <code className="font-mono text-xs">{pkg.manifest_digest.slice(0, 12)}</code>
            </>
          ) : null}
          .
        </p>
        {pkg.list_status && pkg.list_status !== "VERIFIED" ? <Banner tone="danger" title="The list did not verify">{pkg.list_reasons.join(", ") || pkg.list_status}</Banner> : null}
        {pkg.decision_reason ? <p>Reason: {pkg.decision_reason}</p> : null}
        {pkg.ended_reason ? <p>Ended: {pkg.ended_reason}</p> : null}
        {blockedBecauseYouStaged ? <p className="text-ink-muted">You staged this package, so someone else approves and activates it.</p> : null}
        <div className="flex flex-wrap gap-2">
          {(["validate", "approve", "activate"] as const).filter((k) => steps.includes(k)).map((k) => (
            <Button key={k} pending={k === "validate" ? validate.isPending : k === "approve" ? approve.isPending : activate.isPending} onPress={run[k]}>
              {labels[k]}
            </Button>
          ))}
        </div>
        {steps.includes("reject") ? <ReasonAction label="Reject" onSubmit={(r) => reject.mutate(r)} pending={reject.isPending} error={reject.error} /> : null}
        {validate.error ? <Failed error={validate.error} /> : null}
        {approve.error ? <Failed error={approve.error} /> : null}
        {activate.error ? <Failed error={activate.error} /> : null}
      </div>
    </Card>
  );
}

export function TrustStorePage({ me }: { me?: string }) {
  const list = useCellQuery([...TRUST_KEY, "list"], (api) => api.trustStore.packages());
  const rollback = useCellMutation((api, reason: string) => api.trustStore.rollback(reason), { invalidate: [TRUST_KEY], success: "Rolled back" });
  const retire = useCellMutation((api, reason: string) => api.trustStore.retire(reason), { invalidate: [TRUST_KEY], success: "Retired. Nothing is trusted now." });
  const items = list.data?.items ?? [];
  const hasActive = items.some((p) => p.state === "ACTIVE");
  return (
    <>
      <PageHeader
        title="Chip trust store"
        description="The country signing certificates a passport or ID chip may chain to. A list is imported, checked against an anchor you confirmed, approved by a second person and only then activated."
        breadcrumbs={[{ label: "Chip trust store" }]}
      />
      <div className="space-y-4">
        <ActiveBanner />
        {list.isPending && list.fetchStatus !== "idle" ? <LoadingRows rows={4} /> : null}
        {list.isError ? <Failed error={list.error} /> : null}
        {list.data ? (
          <DataTable<TrustPackage>
            caption="Packages"
            rows={items}
            rowKey={(r) => r.package_id}
            empty="No packages yet. Stage one below."
            columns={[
              { key: "seq", header: "No.", pinned: true, render: (r) => r.seq },
              { key: "state", header: "State", render: (r) => <Badge tone={STATE[r.state].tone}>{STATE[r.state].label}</Badge> },
              { key: "certs", header: "Certificates", render: (r) => r.certificate_count },
              { key: "when", header: "Staged", render: (r) => <DateTime value={r.staged_at} /> },
            ]}
          />
        ) : null}
        {hasActive ? (
          <Card title="Roll back or retire" description="A roll back restores the earlier package. A retire stops trusting the active package altogether. Both need a reason.">
            <div className="space-y-3">
              <ReasonAction label="Roll back" onSubmit={(r) => rollback.mutate(r)} pending={rollback.isPending} error={rollback.error} />
              <ReasonAction label="Retire" onSubmit={(r) => retire.mutate(r)} pending={retire.isPending} error={retire.error} />
            </div>
          </Card>
        ) : null}
        {items.map((p) => (
          <PackageCard key={p.package_id} pkg={p} me={me} />
        ))}
        <StageForm onDone={() => void list.refetch()} />
      </div>
    </>
  );
}
