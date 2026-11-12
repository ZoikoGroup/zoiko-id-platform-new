"use client";

import type { ProviderLabFault, UatCase, UatPreview } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, EmptyState, StatusBadge, type Column } from "@zoiko/ui";
import { ShieldAlert } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { AdminQueryView } from "../identity-providers/shared";
import { badge, releaseReads } from "./assurance-status";
import { EvidenceBuilderCard, FixturesCard, ReleaseSigningCard, ReplayCard } from "./assurance-work";

const REQUIREMENTS_KEY = ["admin", "assurance", "requirements"];
const LAB_KEY = ["admin", "assurance", "provider-lab"];
const STATES = ["NOT_RUN", "BLOCKED", "PASS", "FAIL", "INCONCLUSIVE", "EXPIRED", "WAIVED"];

/** The independent assurance board (ZID-UAT-005): coverage, a release preview and the provider lab. Statuses come from signed evidence only. */
export function AssuranceSection() {
  const reqs = useCellQuery(REQUIREMENTS_KEY, (api) => api.assurance.requirements());
  const lab = useCellQuery(LAB_KEY, (api) => api.assurance.providerLab());
  const [releaseId, setReleaseId] = useState("");
  const [preview, setPreview] = useState<UatPreview | null>(null);
  const check = useCellMutation((api, id: string) => api.assurance.preview(id), { onSuccess: (r) => setPreview(r) });

  const columns: Column<UatCase>[] = [
    { key: "id", header: "Case", pinned: true, render: (c) => <span className="font-mono text-xs">{c.case_id}</span> },
    { key: "risk", header: "Risk", render: (c) => c.risk.replace("PROPOSED_", "proposed ") },
    { key: "dep", header: "Needs", render: (c) => c.dependency.toLowerCase().replace("_", " ") },
    { key: "status", header: "Independent status", render: (c) => (<><StatusBadge state={badge(c.status)} size="xs" /> {c.status.toLowerCase().replace("_", " ")}</>) },
    { key: "assertion", header: "Must hold", render: (c) => c.assertion },
  ];
  const faults: Column<ProviderLabFault>[] = [
    { key: "fault", header: "Fault", pinned: true, render: (f) => f.fault.toLowerCase().replaceAll("_", " ") },
    { key: "expected", header: "Required", render: (f) => f.expected.toLowerCase().replaceAll("_", " ") },
    { key: "observed", header: "The platform did", render: (f) => f.observed.toLowerCase().replaceAll("_", " ") },
    { key: "held", header: "Held", render: (f) => <StatusBadge state={f.held ? "ACTIVE" : "REJECTED"} size="xs" /> },
  ];

  return (
    <div className="space-y-6">
      <Banner tone="info" icon={ShieldAlert}>
        A case is settled only by an independent run with a signed evidence manifest. Until then it reads NOT RUN, and a missing run or manifest is never shown as ready. A
        developer test is not an independent result.
      </Banner>

      <Card title="Coverage">
        <AdminQueryView query={reqs}>
          {(r) =>
            r.baseline ? (
              <div className="space-y-4">
                <ul className="flex flex-wrap gap-4 text-sm" aria-label="Cases by status">
                  {STATES.map((s) => (
                    <li key={s}>
                      <StatusBadge state={badge(s)} size="xs" /> {s.toLowerCase().replace("_", " ")}: <strong>{r.counts[s] ?? 0}</strong>
                    </li>
                  ))}
                </ul>
                <DataTable caption="UAT cases" columns={columns} rows={r.cases} rowKey={(c) => c.case_id} />
              </div>
            ) : (
              <EmptyState title="No case register has been imported">Import the case baseline to see coverage. Until then nothing is ready.</EmptyState>
            )
          }
        </AdminQueryView>
      </Card>

      <Card title="Release preview">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (releaseId.trim()) check.mutate(releaseId.trim());
          }}
        >
          <label className="text-sm">
            Release candidate id
            <input className="ml-2 w-80 rounded border px-2 py-1 font-mono text-xs" value={releaseId} onChange={(e) => setReleaseId(e.target.value)} />
          </label>
          <Button type="submit" isDisabled={check.isPending || !releaseId.trim()}>
            Preview
          </Button>
        </form>
        {check.isError ? <ApiErrorView error={check.error} title="The preview could not be read" /> : null}
        {preview ? (
          <div className="mt-4 space-y-2 text-sm">
            <p>
              <StatusBadge state={releaseReads(preview.decision) === "go" ? "ACTIVE" : "REJECTED"} size="xs" /> <strong>{releaseReads(preview.decision) === "go" ? "Go" : "No go"}</strong> ·{" "}
              {preview.decision.reason.toLowerCase().replaceAll("_", " ")} · P0 {preview.decision.p0_pass} of {preview.decision.p0_required} passed
              {preview.locked_reason ? ` · locked: ${preview.locked_reason.toLowerCase().replaceAll("_", " ")}` : ""}
            </p>
            <ul className="space-y-1">
              {preview.decision.gates.map((g) => (
                <li key={g.gate}>
                  <StatusBadge state={g.passed ? "ACTIVE" : "REJECTED"} size="xs" /> <span className="font-medium">{g.gate}</span>
                  {g.reasons.length ? <span className="text-ink-muted"> · {g.reasons.join(", ").toLowerCase().replaceAll("_", " ")}</span> : null}
                </li>
              ))}
            </ul>
            {preview.decision.missing.length ? <p className="text-ink-muted">Missing independent evidence: {preview.decision.missing.join(", ")}</p> : null}
          </div>
        ) : null}
      </Card>

      <FixturesCard />
      <ReplayCard />
      <EvidenceBuilderCard />
      <ReleaseSigningCard />

      <Card title="Provider lab">
        <AdminQueryView query={lab}>
          {(r) => (
            <div className="space-y-2">
              <DataTable caption="Faults injected into the platform's own guards" columns={faults} rows={r.faults} rowKey={(f) => f.fault} />
              <p className="text-xs text-ink-muted">{r.note}</p>
            </div>
          )}
        </AdminQueryView>
      </Card>
    </div>
  );
}
