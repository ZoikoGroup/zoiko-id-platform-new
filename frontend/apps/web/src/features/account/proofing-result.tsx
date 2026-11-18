"use client";

import { Badge, Banner, Card, DateTime, LoadingRows } from "@zoiko/ui";

import { ApiErrorView, useCellQuery } from "@/lib/cell";

import { assertionText, CHECK_LABEL, checkMeaning, headline, RESULT_LABEL } from "./proofing";

/**
 * WF01 screen A5: the result as evidence, not a single word. What was checked, how each check came out, and what the platform holds true.
 * Every row says what it does not prove. Nothing here is shown as verified unless the platform's own decision says so for this purpose.
 */
export function ProofingResult({ verificationId, enabled }: { verificationId: string; enabled: boolean }) {
  const facts = useCellQuery(["verification", "proofing", verificationId], (api) => api.verification.proofing(verificationId), { enabled });
  if (!enabled) return null;
  if (facts.isPending && facts.fetchStatus !== "idle") return <LoadingRows rows={3} />;
  if (facts.isError) return <ApiErrorView error={facts.error} />;
  if (!facts.data) return null;
  const f = facts.data;
  const h = headline(f);
  return (
    <div className="space-y-3">
      <Banner tone={h.tone} title={h.title}>
        {h.body}
      </Banner>
      <Card title="What was checked" description="Each check is shown on its own. One passing does not stand in for another.">
        <ul className="space-y-3 text-sm">
          {f.checks.map((c) => {
            const r = RESULT_LABEL[c.result];
            return (
              <li key={c.type}>
                <span className="font-medium text-ink">{CHECK_LABEL[c.type]}</span> <Badge tone={r.tone}>{r.label}</Badge>
                <p className="mt-0.5 text-ink-muted">{checkMeaning(c.type, c.result)}</p>
              </li>
            );
          })}
        </ul>
        {f.assertions.length ? (
          <p className="mt-3 text-sm text-ink-muted">Held true: {f.assertions.map(assertionText).join("; ")}.</p>
        ) : (
          <p className="mt-3 text-sm text-ink-muted">Nothing is held true yet.</p>
        )}
        <p className="mt-3 text-xs text-ink-muted">
          Purpose {f.scope.replace(/_/g, " ")}, region {f.region}. Policy version {f.policy.version}.{" "}
          {f.decision === "verified_for_scope" ? (
            <>
              Valid until <DateTime value={f.expires_at} />.
            </>
          ) : null}
        </p>
      </Card>
    </div>
  );
}
