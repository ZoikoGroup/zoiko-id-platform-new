"use client";

import type { CredentialDossier } from "@zoiko/api-client";
import { Card, EmptyState, StatusBadge } from "@zoiko/ui";
import { useState } from "react";

import { LookupForm, useCellQuery } from "@/lib/cell";

import { AdminQueryView } from "../identity-providers/shared";

const FACTS: [string, string][] = [
  ["credential_status", "Credential status"],
  ["issuer_authenticity", "Issuer authenticity"],
  ["primary_source_verified", "Primary source verified"],
  ["professional_eligibility", "Professional eligibility"],
  ["current_consumer_entitlement", "Entitlement for this consumer"],
];

function show(v: unknown): string {
  return v === null || v === undefined ? "—" : typeof v === "boolean" ? (v ? "Yes" : "No") : String(v);
}

function words(v: unknown): string {
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.map((t) => (typeof t === "string" ? t : JSON.stringify(t))).join(" ");
  return "";
}

function Dossier({ d }: { d: CredentialDossier }) {
  return (
    <div className="space-y-6">
      <Card title={`Current checks (${d.checks.length})`}>
        {d.checks.length === 0 ? (
          <EmptyState title="No current checks">This person has no credential check on record, or every check has been replaced.</EmptyState>
        ) : (
          <div className="space-y-4">
            {d.checks.map((c) => (
              <div key={c.check_id} className="rounded-card border border-border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-ink">{c.claim_type.replaceAll("_", " ").toLowerCase()}</span>
                  <StatusBadge state={c.decision} size="xs" />
                  <span className="font-mono text-xs text-ink-muted">{c.check_id}</span>
                  <span className="text-xs text-ink-muted">checked {c.checked_at ?? "—"}</span>
                </div>
                <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
                  {FACTS.map(([k, label]) => (
                    <div key={k} className="flex justify-between gap-3">
                      <dt className="text-ink-muted">{label}</dt>
                      <dd className="text-ink">{show(c.assertion[k])}</dd>
                    </div>
                  ))}
                </dl>
                {words(c.assertion.reason_text) ? <p className="mt-2 text-sm text-ink-muted">{words(c.assertion.reason_text)}</p> : null}
              </div>
            ))}
          </div>
        )}
      </Card>
      <Card title={`Decisions (${d.decisions.length})`}>
        {d.decisions.length === 0 ? (
          <EmptyState title="No decisions yet">Eligibility and engagement are decided from the checks above, each by its own policy.</EmptyState>
        ) : (
          <ul className="space-y-1 text-sm">
            {d.decisions.map((x) => (
              <li key={x.decision_id}>
                {x.kind.toLowerCase()} · <StatusBadge state={x.outcome} size="xs" /> · {x.policy_id} v{x.policy_version} · {x.created_at}
                {x.restrictions.length ? ` · restrictions: ${x.restrictions.join(", ")}` : ""}
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title={`Appeals (${d.appeals.length})`}>
        {d.appeals.length === 0 ? (
          <p className="text-sm text-ink-muted">No appeals.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {d.appeals.map((a) => (
              <li key={a.appeal_id}>
                {a.state} · check <span className="font-mono text-xs">{a.check_id}</span> · raised {a.created_at}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

/** WF-02: a reviewer's single view of one person's credential checks. It only reads; decisions are made through their own actions. */
export function DossierSection() {
  const [subject, setSubject] = useState<string | null>(null);
  const q = useCellQuery(["admin", "credentials", "dossier", subject], (api) => api.credentials.dossier(subject!), { enabled: Boolean(subject) });
  return (
    <div className="space-y-4">
      <LookupForm label="Person ID" placeholder="00000000-0000-0000-0000-000000000000" onLookup={setSubject} />
      {subject ? <AdminQueryView query={q}>{(d) => <Dossier d={d} />}</AdminQueryView> : null}
    </div>
  );
}
