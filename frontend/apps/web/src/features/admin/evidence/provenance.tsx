"use client";

import type { EvidenceArtifact, ProvenanceGraph } from "@zoiko/api-client";
import { Banner, Button, Card, Code, DateTime, JsonView, Select, TextField } from "@zoiko/ui";
import { GitBranch } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation } from "@/lib/cell";

/** Provenance graph (EVIDENCE-001 §14): which records a decision, grant or mandate came from. */
function Lineage() {
  const [type, setType] = useState("decision");
  const [ref, setRef] = useState("");
  const [depth, setDepth] = useState("3");
  const trace = useCellMutation((api, v: { t: string; r: string; d: number }) => api.evidence.provenance(v.t, v.r, v.d));
  const g: ProvenanceGraph | undefined = trace.data;
  return (
    <Card title="Lineage" description="Follow an entity to the records it was derived from and the receipts that prove each step.">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-[12rem_1fr_8rem_auto] sm:items-end">
          <TextField label="Entity type" mono value={type} onChange={(v) => setType(v.toLowerCase())} description="e.g. decision, authority_grant, mandate" />
          <TextField label="Entity ID" mono value={ref} onChange={setRef} />
          <Select
            label="Depth"
            value={depth}
            onChange={setDepth}
            options={["1", "2", "3", "4", "5"].map((d) => ({ id: d, label: d }))}
          />
          <Button
            pending={trace.isPending}
            isDisabled={!/^[a-z_]{1,64}$/.test(type) || !ref.trim()}
            onPress={() => trace.mutate({ t: type, r: ref.trim(), d: Number(depth) })}
          >
            Trace
          </Button>
        </div>
        {trace.error ? <ApiErrorView error={trace.error} /> : null}
        {g ? (
          g.edges.length ? (
            <div className="space-y-2">
              <p className="text-xs text-ink-muted">
                {g.nodes.length} records, {g.edges.length} links{g.truncated ? " (truncated: narrow the depth)" : ""}.
              </p>
              <ol className="space-y-1.5">
                {g.edges.map((e, i) => (
                  <li key={`${e.subject_ref}-${e.relation}-${e.object_ref}-${i}`} className="flex flex-wrap items-center gap-2 text-sm">
                    <Code>{`${e.subject_type}:${e.subject_ref}`}</Code>
                    <span className="text-xs font-medium text-brand-700">{e.relation.toLowerCase().replace(/_/g, " ")}</span>
                    <Code>{`${e.object_type}:${e.object_ref}`}</Code>
                    {e.receipt_id ? <span className="text-xs text-ink-muted">receipt {e.receipt_id.slice(0, 8)}…</span> : null}
                    <span className="text-xs text-ink-subtle">
                      <DateTime value={e.recorded_at} />
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          ) : (
            <p className="text-sm text-ink-muted">No provenance is recorded for this entity.</p>
          )
        ) : null}
      </div>
    </Card>
  );
}

/** The artifact types the evidence ledger registers (evidence_0003). */
const ARTIFACT_TYPES = ["POLICY_RELEASE", "ACTION_DEFINITION", "SCHEMA_RELEASE", "JURISDICTION_RULE_PACK", "VERIFICATION_PROFILE", "CREDENTIAL_TRUST_PROFILE", "SIGNAL_RULE_RELEASE", "AGENT_BLUEPRINT", "SIGNING_MATRIX_RELEASE", "CONSENT_NOTICE_VERSION"];

/** Registered artifacts (policy releases, schemas, rule packs) by type and reference. */
function Artifacts() {
  const [type, setType] = useState("POLICY_RELEASE");
  const [ref, setRef] = useState("");
  const find = useCellMutation((api, v: { t: string; r: string }) => api.evidence.artifact(v.t, v.r));
  const items: EvidenceArtifact[] = find.data?.items ?? [];
  return (
    <Card title="Artifacts" description="The exact versions of policies, schemas and configuration a decision was made under.">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-[14rem_1fr_auto] sm:items-end">
          <Select
            label="Artifact type"
            value={type}
            onChange={setType}
            options={ARTIFACT_TYPES.map((t) => ({ id: t, label: t.toLowerCase().replace(/_/g, " ") }))}
          />
          <TextField label="Reference" mono value={ref} onChange={setRef} />
          <Button pending={find.isPending} isDisabled={!/^[A-Z_]{1,64}$/.test(type) || !ref.trim()} onPress={() => find.mutate({ t: type, r: ref.trim() })}>
            Find
          </Button>
        </div>
        {find.error ? <ApiErrorView error={find.error} /> : null}
        {find.data ? (
          items.length ? (
            <ul className="space-y-3">
              {items.map((a) => (
                <li key={a.artifact_id} className="space-y-1 rounded-card border border-border p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium text-ink">v{String(a.version)}</span>
                    <Code>{a.content_hash.slice(0, 24)}…</Code>
                    <span className="text-xs text-ink-muted">
                      registered <DateTime value={a.registered_at} />
                    </span>
                  </div>
                  <JsonView value={a.body} label="Artifact body" />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-muted">No artifact with that type and reference.</p>
          )
        ) : null}
      </div>
    </Card>
  );
}

export function ProvenanceSection() {
  return (
    <div className="space-y-6">
      <Banner tone="info" icon={GitBranch}>
        Provenance shows identifiers and receipts only; personal data stays behind pseudonymous handles.
      </Banner>
      <Lineage />
      <Artifacts />
    </div>
  );
}
