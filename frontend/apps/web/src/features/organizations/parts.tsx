import type { OnboardingItem, OrgApprovalSummary } from "@zoiko/api-client";
import { Badge, ButtonLink, DateTime } from "@zoiko/ui";
import Link from "next/link";
import type { ReactNode } from "react";

import { GOVERNANCE_HREF, itemTone, remediationHref, requestHref, statusTone } from "./logic";

/** The status as a word first and a tone second: never colour alone (WF-013C section 13). */
export function StatusWord({ word, tone }: { word: string; tone: "ok" | "warn" | "danger" | "info" | "neutral" }) {
  return <Badge tone={tone}>{word}</Badge>;
}

/** One line of the setup checklist or the trust header: the plain status word, why it is restricted and what to do next. */
export function ChecklistItem({ item, children }: { item: OnboardingItem; children?: ReactNode }) {
  const href = item.done ? null : remediationHref(item.key);
  return (
    <li className="flex flex-col gap-1 py-3" data-item={item.key}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{item.title}</span>
        <StatusWord word={item.status} tone={itemTone(item)} />
        {!item.required ? <span className="text-xs text-ink-muted">Recommended</span> : null}
      </div>
      {item.reason ? <p className="text-sm text-ink-muted">{item.reason}</p> : null}
      {item.next_action ? (
        <p className="text-sm">
          <span className="text-ink-muted">Next: </span>
          {href ? (
            <Link className="inline-flex min-h-11 items-center text-brand-700 underline-offset-2 hover:underline" href={href}>
              {item.next_action}
            </Link>
          ) : (
            <span className="text-ink">{item.next_action}</span>
          )}
        </p>
      ) : null}
      {children}
    </li>
  );
}

/** An inbox card: action title, requester, A1 or A2, quorum, expiry and next action. No payload and no evidence appear here. */
export function RequestCard({ orgId, r }: { orgId: string; r: OrgApprovalSummary }) {
  return (
    <li className="rounded-lg border border-border bg-surface p-4" data-request={r.request_id}>
      <div className="flex flex-wrap items-center gap-2">
        <Link className="inline-flex min-h-11 items-center text-base font-semibold text-brand-700 underline-offset-2 hover:underline" href={requestHref(orgId, r.request_id)}>
          {r.title}
        </Link>
        <StatusWord word={r.status_label} tone={statusTone(r.state)} />
        <Badge tone={r.level === "A2" ? "danger" : "warn"}>{r.level === "A2" ? "Critical (A2)" : "Sensitive (A1)"}</Badge>
      </div>
      <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs uppercase tracking-wide text-ink-muted">Approvals</dt>
          <dd>
            {r.approvals_received} of {r.approvals_required}
          </dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-ink-muted">Requested by</dt>
          <dd className="text-sm">{r.requester?.label ?? r.requester_principal_id}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-ink-muted">Expires</dt>
          <dd>
            <DateTime value={r.expires_at} />
          </dd>
        </div>
      </dl>
      <p className="mt-2 text-sm text-ink-muted">
        <span className="text-ink">Next: </span>
        {r.next_action}
      </p>
      {r.shortage ? (
        <p className="mt-2 text-sm">
          <strong>{r.shortage}.</strong>{" "}
          <Link className="inline-flex min-h-11 items-center text-brand-700 underline" href={GOVERNANCE_HREF}>
            Invite or appoint independent approvers
          </Link>
          . It is never waived or simulated.
        </p>
      ) : null}
    </li>
  );
}

export function BackLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <ButtonLink href={href} variant="ghost" size="sm" className="min-h-11">
      {children}
    </ButtonLink>
  );
}
