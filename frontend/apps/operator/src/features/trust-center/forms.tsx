"use client";

import { Badge, Banner, Button, Card, DataTable, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { RegError } from "../regulatory/common";

import { evidenceReady, figureReady, splitList } from "./form-logic";

const K = ["trust", "ops"] as const;
const TYPES = ["report", "attestation", "certificate", "assessment", "registration", "self_assessment", "declaration", "contract_commitment", "program_listing"].map((t) => ({ id: t, label: t.replaceAll("_", " ") }));

/** Record an assurance artifact. The digest is the SHA-256 of the exact file; the issuer confirms it later, and not the person who recorded it. */
export function RecordEvidence() {
  const [f, setF] = useState({ digest: "", evidence_type: "report", scheme: "", issuer: "", assessor: "", legal_entity: "", products: "", regions: "", period_start: "", period_end: "", holder: "zoiko", restricted: false });
  const rec = useCellMutation(
    (api, v: typeof f) => api.trustCenter.recordEvidence({ ...v, products: splitList(v.products), regions: splitList(v.regions).map((r) => r.toUpperCase()) }),
    { invalidate: [K as never], success: "Evidence recorded. Someone else must confirm it with the issuer." },
  );
  return (
    <Card title="Record evidence" description="Facts never change once recorded. Say exactly what was assessed: the legal entity, the products, the regions and the period. A scope you leave out is not covered.">
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); rec.mutate(f); }}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Select label="Kind" options={TYPES} value={f.evidence_type} onChange={(v) => setF({ ...f, evidence_type: v })} description="SOC 2 is a report, never a certificate; ISO 27001 needs a certificate." />
          <TextField label="Scheme" isRequired value={f.scheme} onChange={(v) => setF({ ...f, scheme: v })} description="For example SOC 2, ISO 27001, Penetration test." />
          <TextField label="Issuer" isRequired value={f.issuer} onChange={(v) => setF({ ...f, issuer: v })} />
          <TextField label="Assessor" value={f.assessor} onChange={(v) => setF({ ...f, assessor: v })} />
          <TextField label="Legal entity covered" isRequired value={f.legal_entity} onChange={(v) => setF({ ...f, legal_entity: v })} />
          <TextField label="Issued to" isRequired value={f.holder} onChange={(v) => setF({ ...f, holder: v })} description="Type zoiko if it is ours; a supplier's name otherwise." />
          <TextField label="Products covered" isRequired value={f.products} onChange={(v) => setF({ ...f, products: v })} description="Comma separated." />
          <TextField label="Regions covered" value={f.regions} onChange={(v) => setF({ ...f, regions: v })} description="Two-letter codes. Empty means no region is covered." />
          <TextField label="Period starts" type="date" isRequired value={f.period_start} onChange={(v) => setF({ ...f, period_start: v })} />
          <TextField label="Period ends" type="date" isRequired value={f.period_end} onChange={(v) => setF({ ...f, period_end: v })} />
        </div>
        <TextField label="SHA-256 of the file" mono isRequired value={f.digest} onChange={(v) => setF({ ...f, digest: v.trim().toLowerCase() })} description="64 hex characters. The file itself is stored separately and never publicly." />
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.restricted} onChange={(e) => setF({ ...f, restricted: e.target.checked })} /> Restricted: shown only through the document request room</label>
        {rec.error ? <RegError error={rec.error} /> : null}
        <Button type="submit" pending={rec.isPending} isDisabled={!evidenceReady(f)}>Record evidence</Button>
      </form>
    </Card>
  );
}

/** Record one measured availability figure from raw counts. A different person validates it before it is public. */
export function RecordFigure() {
  const [f, setF] = useState({ service: "", region: "", window_label: "", numerator: "", denominator: "", exclusions: "", source_ref: "" });
  const rec = useCellMutation(
    (api, v: typeof f) => api.trustCenter.recordSnapshot({ ...v, numerator: Number(v.numerator), denominator: Number(v.denominator) }),
    { invalidate: [K as never], success: "Figure recorded. A different person must validate it." },
  );
  return (
    <Card title="Record a measured figure" description="Good requests over eligible requests in one counting window, read from raw telemetry. Nothing is public until a second person validates it, and an empty window is never shown as 100%.">
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); rec.mutate(f); }}>
        <div className="grid gap-3 sm:grid-cols-3">
          <TextField label="Service" isRequired value={f.service} onChange={(v) => setF({ ...f, service: v })} />
          <TextField label="Region" isRequired value={f.region} onChange={(v) => setF({ ...f, region: v })} />
          <TextField label="Window" isRequired value={f.window_label} onChange={(v) => setF({ ...f, window_label: v })} description="2026-10 or 2026-10-05." />
          <TextField label="Good" isRequired inputMode="numeric" value={f.numerator} onChange={(v) => setF({ ...f, numerator: v.replace(/\D/g, "") })} />
          <TextField label="Eligible" isRequired inputMode="numeric" value={f.denominator} onChange={(v) => setF({ ...f, denominator: v.replace(/\D/g, "") })} />
          <TextField label="Source" isRequired value={f.source_ref} onChange={(v) => setF({ ...f, source_ref: v })} description="Where the raw counts came from." />
        </div>
        <TextField label="What was excluded" value={f.exclusions} onChange={(v) => setF({ ...f, exclusions: v })} description="For example planned maintenance." />
        {rec.error ? <RegError error={rec.error} /> : null}
        <Button type="submit" pending={rec.isPending} isDisabled={!figureReady(f)}>Record figure</Button>
      </form>
    </Card>
  );
}

/** Public incident advisories. Tied to an incident in the operators' own registry; a second person approves each version before it is public; a correction is a new version with a reason. */
export function Advisories() {
  const q = useCellQuery([...K, "advisories"], (api) => api.trustCenter.advisories());
  const [f, setF] = useState({ incident_ref: "", kind: "PUBLISH", title: "", body: "", reason: "" });
  const draft = useCellMutation((api, v: typeof f) => api.trustCenter.draftAdvisory(v), { invalidate: [K as never], success: "Draft saved. A different person must approve it." });
  const approve = useCellMutation((api, v: { id: string; seq: number }) => api.trustCenter.approveAdvisory(v.id, v.seq), { invalidate: [K as never], success: "Approved: now public" });
  return (
    <div className="space-y-4">
      <Card title="Draft an advisory, update or correction" description="Describe impact, never the way in: no addresses, hostnames, secrets or exploit detail. The first version publishes; later ones update or correct it.">
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); draft.mutate(f); }}>
          <TextField label="Incident id" mono isRequired value={f.incident_ref} onChange={(v) => setF({ ...f, incident_ref: v.trim() })} description="From the incident registry." />
          <Select label="Kind" options={["PUBLISH", "UPDATE", "CORRECTION", "RESOLVE", "POSTMORTEM"].map((k) => ({ id: k, label: k.toLowerCase() }))} value={f.kind} onChange={(v) => setF({ ...f, kind: v })} />
          <TextField label="Title" isRequired value={f.title} onChange={(v) => setF({ ...f, title: v })} />
          <TextField label="Text" isRequired multiline rows={4} value={f.body} onChange={(v) => setF({ ...f, body: v })} />
          {f.kind === "CORRECTION" ? <TextField label="Why the earlier version was wrong" isRequired value={f.reason} onChange={(v) => setF({ ...f, reason: v })} /> : null}
          {draft.error ? <RegError error={draft.error} /> : null}
          <Button type="submit" pending={draft.isPending} isDisabled={f.incident_ref.length < 30 || f.title.trim().length < 3 || f.body.trim().length < 3 || (f.kind === "CORRECTION" && f.reason.trim().length < 3)}>Save draft</Button>
        </form>
      </Card>
      <QueryView query={q}>
        {(d) =>
          d.items.length === 0 ? <Banner tone="info" title="No advisories">Nothing has been drafted.</Banner> : (
            <div className="space-y-3">
              {d.items.map((a) => (
                <Card key={a.advisory_id} title={`Incident ${a.incident_ref}`}>
                  <DataTable
                    caption="Versions"
                    columns={[
                      { key: "s", header: "#", render: (v) => v.seq },
                      { key: "k", header: "Kind", render: (v) => v.kind.toLowerCase() },
                      { key: "t", header: "Title", render: (v) => v.title },
                      { key: "p", header: "State", render: (v) => (v.published ? <Badge tone="ok">public</Badge> : <Button variant="secondary" onPress={() => approve.mutate({ id: a.advisory_id, seq: v.seq })}>Approve</Button>) },
                    ]}
                    rows={a.versions}
                    rowKey={(v) => String(v.seq)}
                  />
                </Card>
              ))}
            </div>
          )
        }
      </QueryView>
      {approve.error ? <RegError error={approve.error} /> : null}
    </div>
  );
}
