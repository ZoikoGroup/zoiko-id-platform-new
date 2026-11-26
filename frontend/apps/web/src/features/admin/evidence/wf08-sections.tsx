"use client";

import type { ExportRequest, ExportTier } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, DateTime, KeyValues, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { axisLines, reasonLine } from "./verification-axes";
import { CLAIMS, LEGAL_BASES, MAX_RETENTION, RECIPIENT_KINDS, TIERS, exportFormProblems, exportNextStep, snapshotHeadline, snapshotReason, trustSummary } from "./wf08-logic";

function save(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name.replace(/[^A-Za-z0-9._-]/g, "-");
  a.click();
  URL.revokeObjectURL(url);
}

/** Who held which authority when a decision was made. Written when the decision was made and never edited. */
export function AuthorityAtDecisionSection() {
  const [ref, setRef] = useState("");
  const [asked, setAsked] = useState("");
  const q = useCellQuery(["admin", "evidence", "authority", asked], (api) => api.evidence.authoritySnapshot(asked), { enabled: asked !== "" });
  return (
    <Card title="Authority at the time of a decision" description="The grants, mandates and sponsor that stood behind a decision at that moment, as recorded then. A later revocation never changes this page.">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <TextField label="Decision reference" value={ref} onChange={setRef} placeholder="the decision or receipt ID" mono />
          <Button isDisabled={!ref.trim()} onPress={() => setAsked(ref.trim())}>
            Show
          </Button>
        </div>
        {asked ? (
          <QueryView query={q} loadingRows={3}>
            {(s) => {
              const h = snapshotHeadline(s);
              return (
                <div className="space-y-4">
                  <Banner tone={h.tone} title={h.text}>
                    {s.reasons.length ? (
                      <ul className="list-disc pl-5">
                        {s.reasons.map((r) => (
                          <li key={r}>{snapshotReason(r)}</li>
                        ))}
                      </ul>
                    ) : (
                      s.body.caveat
                    )}
                  </Banner>
                  <KeyValues
                    items={[
                      ["Decision time", <DateTime value={s.decision_at} />],
                      ["About", s.subject_ref],
                      ["Signed by", s.signer_kid],
                      ["Record digest", <span className="font-mono text-xs break-all">{s.snapshot_digest}</span>],
                    ]}
                  />
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="text-ink-muted">
                        <th className="py-1 pr-3">Kind</th>
                        <th className="pr-3">Source</th>
                        <th className="pr-3">Version</th>
                        <th className="pr-3">Issued by</th>
                        <th className="pr-3">In effect</th>
                        <th>Evidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.body.edges.map((e) => (
                        <tr key={`${e.edge_type}-${e.edge_id}-${e.version}`} className="border-t border-line">
                          <td className="py-1 pr-3">{e.edge_type.toLowerCase().replaceAll("_", " ")}</td>
                          <td className="pr-3 font-mono text-xs">{e.edge_id}</td>
                          <td className="pr-3">{e.version}</td>
                          <td className="pr-3">{e.issuer}</td>
                          <td className="pr-3">
                            {e.effective_from ? <DateTime value={e.effective_from} /> : "?"} to {e.effective_until ? <DateTime value={e.effective_until} /> : "open"}
                          </td>
                          <td className="font-mono text-xs">{e.evidence_digest ? `${e.evidence_digest.slice(0, 16)}…` : "none"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            }}
          </QueryView>
        ) : null}
      </div>
    </Card>
  );
}

/** The issuer's signed trust bundle: each signing key and where it stands. */
export function TrustInspectorSection() {
  const q = useCellQuery(["admin", "evidence", "trust-bundle"], (api) => api.evidence.trustBundle());
  return (
    <Card
      title="Trust inspector"
      description="Which signing keys the issuer says to trust, and from when a key must not be. Check the bundle's own signature with keys you pinned earlier."
      actions={
        q.data ? (
          <Button size="sm" variant="secondary" onPress={() => save("zoiko-trust-bundle.json", q.data)}>
            Download
          </Button>
        ) : null
      }
    >
      <QueryView query={q} loadingRows={3}>
        {(t) => {
          // eslint-disable-next-line react-hooks/purity -- reads the clock to judge the bundle's expiry
          const s = trustSummary(t.bundle, new Date());
          return (
            <div className="space-y-3">
              <Banner tone={s.current ? "info" : "danger"} title={s.note}>
                Version {t.bundle.version}, issued <DateTime value={t.bundle.issued_at} />, expires <DateTime value={t.bundle.expires_at} />. Environment: {t.bundle.environment}.
              </Banner>
              <ul className="space-y-1 text-sm">
                {s.keys.map((k) => (
                  <li key={k.kid}>
                    <span className="font-mono text-xs">{k.kid}</span>: {k.standing}
                  </li>
                ))}
              </ul>
              <p className="text-sm text-ink-muted">Post-quantum or hybrid signing: {t.bundle.pq_hybrid.toLowerCase().replaceAll("_", " ")}. {t.bundle.limits}</p>
            </div>
          );
        }}
      </QueryView>
    </Card>
  );
}

/** Revoke, suspend and reinstate through a signed status list, and publish it. */
export function StatusListSection() {
  const [purpose, setPurpose] = useState("revocation");
  const [subject, setSubject] = useState("");
  const [reason, setReason] = useState("");
  const [last, setLast] = useState<string>("");
  const alloc = useCellMutation((api) => api.evidence.allocateStatusSlot(purpose, subject.trim()), { success: "Position assigned", onSuccess: (r) => setLast(`Position ${r.position} of ${r.size_bits}`) });
  const revoke = useCellMutation((api) => api.evidence.revokeStatus(purpose, subject.trim(), reason.trim()), { success: "Recorded. Publish to make it visible." });
  const reinstate = useCellMutation((api) => api.evidence.reinstateStatus(purpose, subject.trim(), reason.trim()), { success: "Reinstated. Publish to make it visible." });
  const publish = useCellMutation((api) => api.evidence.publishStatusList(purpose), { success: "Published", onSuccess: (r) => setLast(`Version ${r.version}, valid until ${r.expires_at}`) });
  const ready = subject.trim() !== "" && reason.trim().length >= 3;
  const err = alloc.error ?? revoke.error ?? reinstate.error ?? publish.error;
  return (
    <Card title="Status list" description="Each credential or agent has a random position in one large signed list. Anyone can download the list and look their own position up, so the platform never learns which one is being checked.">
      <div className="space-y-4">
        <Banner tone="info" title="A list is only as fresh as its last publication">
          A relying party that finds the list older than the age it accepts must treat the status as unknown, never as active. Changes below take effect for others only after you publish.
        </Banner>
        <div className="grid gap-3 sm:grid-cols-2">
          <Select label="List" value={purpose} onChange={setPurpose} options={[{ id: "revocation", label: "Revocation" }, { id: "suspension", label: "Suspension" }]} />
          <TextField label="Credential or agent" value={subject} onChange={setSubject} placeholder="the agent or credential ID" mono />
          <TextField label="Reason" value={reason} onChange={setReason} placeholder="Why this change" />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" isDisabled={!subject.trim()} pending={alloc.isPending} onPress={() => alloc.mutate(undefined)}>
            Give it a position
          </Button>
          <Button variant="danger" isDisabled={!ready} pending={revoke.isPending} onPress={() => revoke.mutate(undefined)}>
            {purpose === "suspension" ? "Suspend" : "Revoke"}
          </Button>
          <Button variant="secondary" isDisabled={!ready} pending={reinstate.isPending} onPress={() => reinstate.mutate(undefined)}>
            Reinstate
          </Button>
          <Button pending={publish.isPending} onPress={() => publish.mutate(undefined)}>
            Publish the list
          </Button>
        </div>
        {last ? <p className="text-sm text-ink-muted">{last}</p> : null}
        {err ? <ApiErrorView error={err} /> : null}
      </div>
    </Card>
  );
}

/** Take a receipt apart: the verification axes, then the authority at the time, in one place. */
export function ForensicSection() {
  const [id, setId] = useState("");
  const [asked, setAsked] = useState("");
  const verify = useCellQuery(["admin", "evidence", "forensic", "verify", asked], (api) => api.evidence.verify(asked), { enabled: asked !== "" });
  const replay = useCellQuery(["admin", "evidence", "forensic", "replay", asked], (api) => api.evidence.replay(asked), { enabled: asked !== "" });
  return (
    <Card title="Forensic reconstruction" description="Re-check a receipt and replay the decision. The result is a set of separate statements. Replay shows whether the decision still comes out the same, not whether it was right.">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <TextField label="Receipt ID" value={id} onChange={setId} mono />
          <Button isDisabled={!id.trim()} onPress={() => setAsked(id.trim())}>
            Reconstruct
          </Button>
        </div>
        {asked ? (
          <div className="grid gap-4 lg:grid-cols-2">
            <QueryView query={verify} loadingRows={3}>
              {(v) =>
                v.axes ? (
                  <div className="space-y-2">
                    <KeyValues items={axisLines(v.axes).map((l): [string, string] => [l.label, l.value])} />
                    <ul className="list-disc pl-5 text-sm text-ink-muted">
                      {v.axes.reasons.map((r) => (
                        <li key={r}>{reasonLine(r)}</li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <p className="text-sm text-ink-muted">This cell did not return the separate statements.</p>
                )
              }
            </QueryView>
            <QueryView query={replay} loadingRows={3}>
              {(r) => <pre className="max-h-64 overflow-auto rounded bg-surface-sunken p-3 text-xs">{JSON.stringify(r, null, 2)}</pre>}
            </QueryView>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

const KEY = ["admin", "evidence", "export-requests"];

function RequestForm() {
  const [tier, setTier] = useState<ExportTier>("CUSTOMER_EVIDENCE");
  const [f, setF] = useState({ receiptId: "", recipientName: "", recipientKind: "", contact: "", purpose: "", legalBasis: "", country: "", retention: 30 });
  const form = { tier, ...f };
  const problems = exportFormProblems(form);
  const create = useCellMutation(
    (api) =>
      api.evidence.requestExport({
        receipt_id: f.receiptId.trim(),
        tier,
        recipient: tier === "PUBLIC_MINIMAL" ? null : { name: f.recipientName.trim(), kind: f.recipientKind, contact: f.contact.trim() },
        purpose: f.purpose.trim(),
        legal_basis: tier === "PUBLIC_MINIMAL" ? null : f.legalBasis,
        retention_days: f.retention,
        recipient_country: tier === "PUBLIC_MINIMAL" ? null : f.country.trim().toUpperCase(),
      }),
    { invalidate: [KEY], success: "Request recorded" },
  );
  const set = (k: keyof typeof f) => (v: string) => setF((o) => ({ ...o, [k]: k === "retention" ? Number(v) || 0 : v }));
  const t = TIERS.find((x) => x.id === tier)!;
  return (
    <Card title="Ask for an export" description="Choose how much leaves the platform. A private tier names who receives it, why, and on what basis.">
      <div className="space-y-3">
        <Select label="Tier" value={tier} onChange={(v) => setTier(v as ExportTier)} options={TIERS.map((x) => ({ id: x.id, label: x.label }))} />
        <p className="text-sm text-ink-muted">{t.promise}</p>
        <TextField label="Receipt ID" value={f.receiptId} onChange={set("receiptId")} mono />
        {t.needsRecipient ? (
          <>
            <TextField label="Recipient" value={f.recipientName} onChange={set("recipientName")} />
            <Select label="Kind of recipient" value={f.recipientKind} onChange={set("recipientKind")} options={RECIPIENT_KINDS[tier]} />
            <TextField label="Recipient contact" value={f.contact} onChange={set("contact")} />
            <TextField label="Purpose" value={f.purpose} onChange={set("purpose")} />
            <Select label="Legal basis" value={f.legalBasis} onChange={set("legalBasis")} options={LEGAL_BASES.map((b) => ({ id: b, label: b.toLowerCase().replaceAll("_", " ") }))} />
            <TextField label="Recipient country (two letters)" value={f.country} onChange={set("country")} />
          </>
        ) : null}
        <TextField label={`Keep for (days, up to ${MAX_RETENTION[tier]})`} value={String(f.retention)} onChange={set("retention")} />
        {problems.length ? <p className="text-sm text-ink-muted">{problems[0]}</p> : null}
        <Button isDisabled={problems.length > 0} pending={create.isPending} onPress={() => create.mutate(undefined)}>
          Send request
        </Button>
        {create.error ? <ApiErrorView error={create.error} /> : null}
      </div>
    </Card>
  );
}

export function ExportRequestsSection() {
  const list = useCellQuery(KEY, (api) => api.evidence.exportRequests());
  const approve = useCellMutation((api, id: string) => api.evidence.approveExport(id), { invalidate: [KEY], success: "Approved" });
  const refuse = useCellMutation((api, v: { id: string; reason: string }) => api.evidence.refuseExport(v.id, v.reason), { invalidate: [KEY], success: "Refused" });
  const issue = useCellMutation((api, id: string) => api.evidence.issueExport(id), { invalidate: [KEY], success: "Issued", onSuccess: (pkg, id) => save(`evidence-export-${id}.json`, pkg) });
  const columns: Column<ExportRequest>[] = [
    { key: "tier", header: "Tier", pinned: true, render: (r) => TIERS.find((t) => t.id === r.tier)?.label ?? r.tier },
    { key: "to", header: "Recipient", render: (r) => (r.recipient.name ? `${r.recipient.name} (${(r.recipient.kind ?? "").toLowerCase()})` : "none") },
    { key: "basis", header: "Basis", render: (r) => (r.legal_basis ? r.legal_basis.toLowerCase().replaceAll("_", " ") : "") },
    { key: "border", header: "Crosses a border", render: (r) => (r.cross_border ? "yes" : "no") },
    { key: "state", header: "State", render: (r) => <StatusBadge state={r.state} kind="Export request" /> },
    { key: "at", header: "Asked", render: (r) => <DateTime value={r.requested_at} /> },
    {
      key: "act",
      header: "Next",
      align: "end",
      render: (r) => {
        const n = exportNextStep(r, null);
        return (
          <div className="flex items-center justify-end gap-2">
            <span className="text-xs text-ink-muted">{n.why}</span>
            {n.approve ? (
              <>
                <Button size="xs" variant="secondary" onPress={() => approve.mutate(r.request_id)}>
                  Approve
                </Button>
                <Button size="xs" variant="secondary" onPress={() => refuse.mutate({ id: r.request_id, reason: "Not approved" })}>
                  Refuse
                </Button>
              </>
            ) : null}
            {n.issue ? (
              <Button size="xs" onPress={() => issue.mutate(r.request_id)}>
                Issue
              </Button>
            ) : null}
          </div>
        );
      },
    },
  ];
  return (
    <div className="space-y-5">
      <RequestForm />
      <Card title="Export requests" description="Each tier hands over a different amount. A dossier, or any transfer across a border, needs a second person, never the one who asked.">
        <QueryView query={list} loadingRows={3}>
          {(d) => <DataTable caption="Export requests" columns={columns} rows={d.items} rowKey={(r) => r.request_id} />}
        </QueryView>
        {(approve.error ?? refuse.error ?? issue.error) ? <ApiErrorView error={(approve.error ?? refuse.error ?? issue.error)!} /> : null}
      </Card>
    </div>
  );
}

/** What may and may not be said about this evidence, and what backs each statement. */
export function ClaimsRegisterSection() {
  return (
    <Card title="Claims register" description="What we may say about this evidence, and what backs it. Anything marked no or not yet must not be said in marketing, contracts or answers to auditors.">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-ink-muted">
            <th className="py-1 pr-3">Claim</th>
            <th className="pr-3">May we say it?</th>
            <th className="pr-3">What backs it</th>
            <th>What is missing</th>
          </tr>
        </thead>
        <tbody>
          {CLAIMS.map((c) => (
            <tr key={c.claim} className="border-t border-line align-top">
              <td className="py-1 pr-3">{c.claim}</td>
              <td className="pr-3 font-medium">{c.allowed}</td>
              <td className="pr-3">{c.backing}</td>
              <td>{c.missing}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
