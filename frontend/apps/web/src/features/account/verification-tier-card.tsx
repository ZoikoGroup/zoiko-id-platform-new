"use client";

import type { VerificationClaimView, VerificationTier } from "@zoiko/api-client";
import { Badge, Banner, Card, Checkbox, DataTable, EmptyState, type Column } from "@zoiko/ui";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { ALTERNATIVE_COPY, CANNOT_OPTIONS, TIER_TONE, checkCopy, claimLabel, revocationText, toggle } from "./verification-tier";

/** Where the person is on the risk tiers and what the next one needs (ZID-EDR-006). R0 says "Not identity verified" and means it: an email
 * confirmation, a phone code, single sign-on or control of a domain is never shown as proof of who a person is. */
export function VerificationTierCard() {
  const [cannot, setCannot] = useState<string[]>([]);
  const tier = useCellQuery(["verification", "tier", cannot.join(",")], (api) => api.verification.tier(cannot));
  return (
    <QueryView query={tier}>
      {(t) => (
        <div className="space-y-4">
          <Standing t={t} />
          <Card title="If you can't do the usual check" description="Tell us what you can't use. The approved alternatives appear; the standard check stays available to everyone.">
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              {CANNOT_OPTIONS.map((o) => (
                <Checkbox key={o.id} isSelected={cannot.includes(o.id)} onChange={() => setCannot((c) => toggle(c, o.id))}>
                  {o.label}
                </Checkbox>
              ))}
            </div>
            {t.alternatives.length ? (
              <ul className="mt-4 space-y-2" aria-label="Approved alternatives">
                {t.alternatives.map((a) => (
                  <li key={a.id} className="rounded-md border border-border p-3 text-sm">
                    <span className="font-medium">{ALTERNATIVE_COPY[a.id]?.label ?? a.id}</span>
                    <span className="mt-0.5 block text-ink-muted">{ALTERNATIVE_COPY[a.id]?.help ?? a.effect}</span>
                    <span className="mt-1 block text-xs text-ink-subtle">
                      It is a separate process with a person involved. Until it is completed you stay at your current level; it does not give you a lower one.
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </Card>
          <Claims claims={t.claims} />
        </div>
      )}
    </QueryView>
  );
}

function Standing({ t }: { t: VerificationTier }) {
  return (
    <Card title="Your verification level">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone={TIER_TONE[t.tier]}>{t.tier === "NONE" ? "No level yet" : `${t.tier}: ${t.label}`}</Badge>
        <p className="text-sm text-ink-muted">{t.meaning}</p>
      </div>
      {t.next_tier ? (
        <div className="mt-4">
          <h3 className="mb-1 text-sm font-medium text-ink">To reach {t.next_tier}</h3>
          <ul className="space-y-1.5 text-sm">
            {t.missing.map((m) => (
              <li key={m.check}>
                <span className="font-medium">{checkCopy(m.check).label}</span>
                {checkCopy(m.check).help ? <span className="text-ink-muted"> · {checkCopy(m.check).help}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="mt-3 text-sm text-ink-muted">This is the highest level.</p>
      )}
      <Banner tone="info" className="mt-4">
        {t.note} An address or a phone number is asked for only when a rule or a workflow you use needs it, and one you type is shown as not verified.
      </Banner>
    </Card>
  );
}

function Claims({ claims }: { claims: VerificationClaimView[] }) {
  const columns: Column<VerificationClaimView>[] = [
    { key: "claim", header: "Claim", pinned: true, render: (c) => <span className="capitalize">{claimLabel(c.claim)}</span>, sortValue: (c) => c.claim },
    { key: "method", header: "How", render: (c) => c.method.replace(/_/g, " ").toLowerCase() },
    { key: "source", header: "Checked by", render: (c) => c.source ?? "—" },
    { key: "policy", header: "Policy", render: (c) => `${c.policy.level}${c.policy.profile ? ` · ${c.policy.profile}${c.policy.version ? ` v${c.policy.version}` : ""}` : ""}` },
    { key: "scope", header: "Used for", render: (c) => (c.scope.length ? c.scope.join(", ") : "—") },
    { key: "issued", header: "Issued", render: (c) => (c.issued_at ? c.issued_at.slice(0, 10) : "—"), sortValue: (c) => c.issued_at ?? "" },
    { key: "until", header: "Valid until", render: (c) => (c.valid_until ? c.valid_until.slice(0, 10) : "No end date"), sortValue: (c) => c.valid_until ?? "" },
    { key: "state", header: "Status", render: (c) => revocationText(c) },
  ];
  return (
    <Card title="What has been verified about you" description="Every claim says how it was checked, by whom, under which policy, what it may be used for, and when it ends." padded={false}>
      <DataTable<VerificationClaimView>
        caption="Verified claims"
        columns={columns}
        rows={claims}
        rowKey={(c) => c.revocation.assertion_id}
        empty={<EmptyState title="Nothing is verified yet" icon={ShieldCheck}>Nothing is shown as verified until a check has succeeded.</EmptyState>}
      />
    </Card>
  );
}
