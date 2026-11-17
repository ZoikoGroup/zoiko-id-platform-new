"use client";

import { ApiError, type EngineComponent } from "@zoiko/api-client";
import { AccessUnavailable, Badge, Banner, Card, DataTable, EmptyState, LoadingRows, PageHeader } from "@zoiko/ui";

import { ApiErrorView, useCellQuery } from "@/lib/cell";

import { groupByOwner, licenceState, MODE_WORDS, percent, READINESS_KEY } from "./logic";

function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 403) return <EmptyState title="Withheld for your role">Your role does not include this.</EmptyState>;
  if (error instanceof ApiError && error.status === 404) return <AccessUnavailable correlationId={error.correlationId} />;
  return <ApiErrorView error={error} />;
}

export function EngineReadinessPage() {
  const q = useCellQuery(READINESS_KEY, (api) => api.verification.engineReadiness());
  const r = q.data;
  const groups = r ? groupByOwner(r.problems_for_approval) : [];
  return (
    <>
      <PageHeader
        title="Face engine readiness"
        description="Whether a face-matching engine could be approved today, and exactly what is missing. This page only reports: an engine is approved by the people who own the evaluation, the licences and the privacy assessment, through a reviewed change to the engine record."
        breadcrumbs={[{ label: "Face engine readiness" }]}
      />
      <div className="space-y-4">
        {q.isPending && q.fetchStatus !== "idle" ? <LoadingRows rows={4} /> : null}
        {q.isError ? <Failed error={q.error} /> : null}
        {r ? (
          <>
            <Banner tone={r.mode === "APPROVED_FOR_SCOPE" && r.approvable ? "ok" : r.mode === "DISABLED" ? "info" : "warn"} title={`Mode: ${MODE_WORDS[r.mode].label}`}>
              {MODE_WORDS[r.mode].text}
              {r.countries.length ? ` Countries: ${r.countries.join(", ")}.` : ""}
            </Banner>
            {r.approvable ? (
              <Banner tone="ok" title="Nothing is missing from the record">
                Every reference the platform checks is present. That says the record is complete, not that the engine is good: that is the evaluation&apos;s and the assessors&apos; judgement.
              </Banner>
            ) : (
              <Card title={`${r.problems_for_approval.length} things keep an engine from being approved`} description="Grouped by who normally closes them.">
                <div className="space-y-4 text-sm">
                  {groups.map((g) => (
                    <section key={g.owner} aria-label={g.owner}>
                      <h3 className="font-medium text-ink">{g.owner}</h3>
                      <ul className="list-disc space-y-1 pl-5">
                        {g.items.map((i) => (
                          <li key={i.code}>{i.text}</li>
                        ))}
                      </ul>
                    </section>
                  ))}
                </div>
              </Card>
            )}
            <Card title="The engine record" description="As the versioned configuration states it.">
              <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
                <Fact label="Engine" value={r.engine.id ? `${r.engine.id} ${r.engine.version ?? ""}`.trim() : null} />
                <Fact label="Evaluation reference" value={r.engine.evaluation_id} />
                <Fact label="Evaluated on" value={r.engine.evaluated_on} />
                <Fact label="Presentation-attack standard" value={r.engine.pad_standard} />
                <Fact label="Privacy assessment (DPIA)" value={r.engine.dpia_ref} />
                <Fact label="Licences read" value={r.engine.licence_checked ? "Recorded" : null} />
                <Fact label="False accept rate" value={r.engine.measured.false_accept === null ? null : `${percent(r.engine.measured.false_accept)} (ceiling ${percent(r.ceilings.false_accept)})`} />
                <Fact label="False reject rate" value={r.engine.measured.false_reject === null ? null : `${percent(r.engine.measured.false_reject)} (ceiling ${percent(r.ceilings.false_reject)})`} />
                <Fact label="Attack accept rate" value={r.engine.measured.attack_accept === null ? null : `${percent(r.engine.measured.attack_accept)} (ceiling ${percent(r.ceilings.attack_accept)})`} />
                <Fact label="Genuine-user reject rate" value={r.engine.measured.bona_fide_reject === null ? null : `${percent(r.engine.measured.bona_fide_reject)} (ceiling ${percent(r.ceilings.bona_fide_reject)})`} />
              </dl>
            </Card>
            <DataTable<EngineComponent>
              caption={`Licence register (version ${r.register.version ?? "unknown"}, ${r.register.status ?? "unknown"})`}
              rows={r.register.components}
              rowKey={(c) => c.id}
              empty="The licence register lists no components."
              columns={[
                { key: "id", header: "Component", pinned: true, render: (c) => c.id },
                { key: "role", header: "Role", render: (c) => c.role ?? "" },
                { key: "licence", header: "Code licence", render: (c) => c.code_licence ?? "" },
                { key: "state", header: "Licence record", render: (c) => <Badge tone={licenceState(c).tone}>{licenceState(c).label}</Badge> },
                { key: "used", header: "In this engine", render: (c) => (c.in_engine ? "Yes" : "No") },
              ]}
            />
          </>
        ) : null}
      </div>
    </>
  );
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-ink-muted">{label}</dt>
      <dd className="text-ink">{value ?? <span className="text-ink-muted">not recorded</span>}</dd>
    </div>
  );
}
