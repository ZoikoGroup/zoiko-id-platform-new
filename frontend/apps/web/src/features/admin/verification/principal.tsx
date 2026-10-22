"use client";

import type { VerificationAssertion } from "@zoiko/api-client";
import { AssuranceBadge, Button, Card, CopyId, DataTable, DateTime, EmptyState, KeyValues, StatusBadge, humanize, type Column } from "@zoiko/ui";
import { BadgeCheck, UserSearch } from "lucide-react";
import { useState } from "react";

import { LookupForm, useCellMutation, useCellQuery } from "@/lib/cell";

import { AdminErrorView, AdminQueryView, R3ActionDialog, UUID_RE, joined } from "../identity-providers/shared";

type ClaimSummary = { level?: string; method?: string; valid_until?: string | null };

/** A principal's current identity assurance and every verification assertion behind it. */
export function PrincipalAssurancePanel() {
  const [principalId, setPrincipalId] = useState<string | null>(null);
  const [badId, setBadId] = useState(false);
  const [target, setTarget] = useState<VerificationAssertion | null>(null);

  const assuranceKey = ["admin", "verification", "assurance", principalId];
  const assertionsKey = ["admin", "verification", "assertions", principalId];
  const assurance = useCellQuery(assuranceKey, (api) => api.verification.assurance(principalId!), { enabled: Boolean(principalId) });
  const assertions = useCellQuery(assertionsKey, (api) => api.verification.assertions(principalId!), { enabled: Boolean(principalId) });

  const revoke = useCellMutation((api, id: string) => api.verification.revokeAssertion(id), {
    invalidate: [assuranceKey, assertionsKey],
    success: "Assertion revoked",
  });

  const columns: Column<VerificationAssertion>[] = [
    {
      key: "claim",
      header: "Claim",
      pinned: true,
      sortValue: (a) => a.claim_type,
      render: (a) => (
        <div className="min-w-0">
          <div className="font-mono text-xs font-medium text-ink">{a.claim_type}</div>
          <CopyId value={a.assertion_id} label="assertion" />
        </div>
      ),
    },
    { key: "level", header: "Level", sortValue: (a) => a.assurance_level, render: (a) => <AssuranceBadge kind="Identity" level={a.assurance_level} /> },
    { key: "status", header: "Status", sortValue: (a) => a.status, render: (a) => <StatusBadge state={a.status} kind="Assertion" /> },
    { key: "method", header: "Method", render: (a) => <span className="text-xs">{humanize(a.verification_method)}</span> },
    { key: "evidence", header: "Evidence", render: (a) => joined(a.evidence_types) },
    { key: "verified", header: "Verified", sortValue: (a) => a.verified_at ?? "", render: (a) => <DateTime value={a.verified_at} /> },
    { key: "fresh", header: "Fresh until", sortValue: (a) => a.fresh_until ?? "", render: (a) => <DateTime value={a.fresh_until} /> },
    { key: "reasons", header: "Reason codes", defaultHidden: true, render: (a) => joined(a.reason_codes) },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (a) =>
        a.status === "VALID" ? (
          <Button size="xs" variant="danger-outline" onPress={() => setTarget(a)}>
            Revoke
          </Button>
        ) : null,
    },
  ];

  const claimEntries = Object.entries((assurance.data?.identity_proofing?.claims ?? {}) as Record<string, ClaimSummary>);

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <UserSearch aria-hidden className="size-4 text-ink-muted" /> Principal assurance
        </span>
      }
      description="Look up a principal's identity assurance and the verification assertions behind it. Values and evidence are never shown."
    >
      <div className="space-y-5">
        <LookupForm
          label="Principal ID"
          placeholder="00000000-0000-0000-0000-000000000000"
          onLookup={(id) => {
            const ok = UUID_RE.test(id);
            setBadId(!ok);
            setPrincipalId(ok ? id : null);
          }}
        />
        {badId ? <AdminErrorView error={new Error("Principal ID must be a UUID.")} /> : null}

        {principalId ? (
          <>
            <section aria-labelledby="pa-summary" className="space-y-3">
              <h3 id="pa-summary" className="text-sm font-semibold text-ink">
                Current assurance
              </h3>
              <AdminQueryView query={assurance}>
                {(a) => (
                  <KeyValues
                    columns={3}
                    items={[
                      ["Principal", <CopyId key="p" value={a.principal_id} label="principal" />],
                      ["Identity assurance", <AssuranceBadge key="l" kind="Identity" level={a.identity_proofing?.level ?? null} />],
                      [
                        "Verified claims",
                        claimEntries.length ? (
                          <ul key="c" className="space-y-1">
                            {claimEntries.map(([claim, c]) => (
                              <li key={claim} className="flex flex-wrap items-center gap-2 text-xs">
                                <span className="font-mono">{claim}</span>
                                {c.level ? <AssuranceBadge kind="Identity" level={c.level} /> : null}
                                {c.valid_until ? (
                                  <span className="text-ink-subtle">
                                    until <DateTime value={c.valid_until} />
                                  </span>
                                ) : null}
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <span key="n" className="text-ink-subtle">None current</span>
                        ),
                      ],
                    ]}
                  />
                )}
              </AdminQueryView>
            </section>

            <section aria-labelledby="pa-assertions" className="space-y-3">
              <h3 id="pa-assertions" className="text-sm font-semibold text-ink">
                Assertions
              </h3>
              <div className="overflow-hidden rounded-lg border border-border">
                <AdminQueryView query={assertions}>
                  {(data) => (
                    <DataTable
                      caption="Verification assertions"
                      columns={columns}
                      rows={data.items}
                      rowKey={(a) => a.assertion_id}
                      dense
                      empty={
                        <EmptyState icon={BadgeCheck} title="No assertions" compact>
                          This principal has never completed a verification.
                        </EmptyState>
                      }
                    />
                  )}
                </AdminQueryView>
              </div>
            </section>
          </>
        ) : null}
      </div>

      {target ? (
        <R3ActionDialog
          isOpen
          onClose={() => setTarget(null)}
          risk="R2"
          title={`Revoke ${target.claim_type} assertion`}
          intent={
            <p>
              The assertion stops counting toward the principal&apos;s assurance immediately, and future reliance decisions will not use it. The
              principal may need to verify again.
            </p>
          }
          scope={[
            ["Claim", <span key="c" className="font-mono">{target.claim_type}</span>],
            ["Level", target.assurance_level],
            ["Assertion", <CopyId key="a" value={target.assertion_id} label="assertion" />],
          ]}
          reversibility="irreversible"
          commitLabel="Revoke assertion"
          run={async () => {
            const r = await revoke.mutateAsync(target.assertion_id);
            return { outcome: r.status, objectId: r.assertion_id };
          }}
        />
      ) : null}
    </Card>
  );
}
