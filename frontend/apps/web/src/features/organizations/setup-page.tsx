"use client";

import { ApiError, type OrgOnboarding } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DateTime, EmptyState, LoadingRows, PageHeader } from "@zoiko/ui";
import { Rocket, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { READINESS_WORDS, SOLO_MESSAGE, ackKey, blockerLines, inboxHref, remediationHref, remediationKeyFor } from "./logic";
import { ChecklistItem } from "./parts";
import { useOrgId } from "./shared";

const KEY = ["org-onboarding"];

/** WF-13C-01: separate completed verification facts from setup tasks, say why anything is restricted, and activate only when the server says so. */
export function SetupPage({ orgParam }: { orgParam: string }) {
  const { orgId, loading, error } = useOrgId(orgParam);
  const q = useCellQuery([...KEY, orgId], (api) => api.org.onboarding(orgId as string), { enabled: Boolean(orgId), refetchInterval: 20_000 });
  if (!orgId) {
    if (loading) return <LoadingRows rows={4} />;
    return error ? <ApiErrorView error={error} /> : <EmptyState title="No organization">Sign in to an organization to see its setup.</EmptyState>;
  }
  return (
    <div className="space-y-6">
      <PageHeader
        icon={Rocket}
        title="Organization setup"
        description="Verification facts come from the trust services and cannot be edited here. Setup steps are yours to complete. Nothing on this page can be bought or skipped."
        breadcrumbs={[{ label: "Company" }, { label: "Setup" }]}
        actions={
          <Link className="inline-flex min-h-11 items-center text-sm text-brand-700 underline-offset-2 hover:underline" href={inboxHref(orgId)}>
            Approvals
          </Link>
        }
      />
      <QueryView query={q}>{(d) => <Checklist orgId={orgId} d={d} />}</QueryView>
    </div>
  );
}

function Checklist({ orgId, d }: { orgId: string; d: OrgOnboarding }) {
  const word = READINESS_WORDS[d.readiness];
  const [blocked, setBlocked] = useState<string[] | null>(null);
  const accept = useCellMutation((api, a: { kind: OrgOnboarding["acknowledgements"][number]["kind"]; version: string }) =>
    api.org.acknowledge(orgId, { kind: a.kind, version: a.version, locale: typeof navigator === "undefined" ? undefined : navigator.language }, ackKey(a.kind, a.version)), {
    invalidate: [KEY],
    success: "Recorded",
  });
  const activate = useCellMutation((api) => api.org.activate(orgId), {
    invalidate: [KEY],
    success: (r) => (r.activated_now ? "Organization activated" : "Already active"),
    onSuccess: () => setBlocked(null),
  });
  const refusal = activate.error instanceof ApiError && activate.error.code === "ACTIVATION_BLOCKED" ? activate.error : null;
  const reasons = blocked ?? (refusal ? blockerLines(refusal.details, d.blockers) : d.blockers);
  const accepted = Object.fromEntries(d.acknowledgements.map((a) => [a.kind, a]));

  return (
    <>
      <Banner tone={word.tone} title={`${word.title}`} live="polite">
        {word.body}
        {d.activated_at ? (
          <span className="mt-1 block text-xs">
            Activated <DateTime value={d.activated_at} />. Data region {d.region}. Policy {d.policy_version}.
          </span>
        ) : (
          <span className="mt-1 block text-xs">
            Data region {d.region}. Policy {d.policy_version}.
          </span>
        )}
      </Banner>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="What has been verified" description="Read-only assertions from the trust services. A register match or a payment never completes one.">
          <ul className="divide-y divide-border" aria-label="Verification checks">
            {d.trust.map((i) => (
              <ChecklistItem key={i.key} item={i} />
            ))}
          </ul>
        </Card>

        <Card title="Required setup" description="Server-derived. These cannot be hidden or switched off.">
          <ul className="divide-y divide-border" aria-label="Required setup">
            {d.required.map((i) => {
              const ack = i.key === "controls" ? accepted.ORGANIZATIONAL_CONTROLS : i.key === "region_policy" ? accepted.REGION_POLICY : undefined;
              return (
                <ChecklistItem key={i.key} item={i}>
                  {ack && !ack.accepted ? (
                    <div className="mt-2 rounded-md bg-sunken p-3 text-sm">
                      <p className="font-medium text-ink">
                        {ack.title} <span className="text-xs font-normal text-ink-muted">version {ack.version}</span>
                      </p>
                      <p className="mt-1 text-ink-muted">{ack.summary}</p>
                      <Button className="mt-2 min-h-11" pending={accept.isPending} onPress={() => accept.mutate({ kind: ack.kind, version: ack.version })}>
                        Accept {ack.title.toLowerCase()}
                      </Button>
                    </div>
                  ) : null}
                  {ack?.accepted ? <p className="text-xs text-ink-muted">Accepted version {ack.version}.</p> : null}
                </ChecklistItem>
              );
            })}
          </ul>
          {accept.error ? <div className="mt-3"><ApiErrorView error={accept.error} title="That could not be recorded" /></div> : null}
        </Card>
      </div>

      <Card title="Independent approvers" description="Recommended. Required before sensitive (A1) and critical (A2) changes can run. Ordinary use never waits for this.">
        <ul aria-label="Approver readiness">
          <ChecklistItem item={d.approvers} />
        </ul>
        {d.solo ? (
          <p className="mt-2 text-sm text-ink-muted" data-solo>
            {SOLO_MESSAGE} You will be told what is needed; no second user is created for you.
          </p>
        ) : null}
      </Card>

      <Card title="Activation" footer={undefined}>
        {d.readiness === "ACTIVE" ? (
          <p className="flex items-center gap-2 text-sm text-ink">
            <ShieldCheck aria-hidden className="size-4" /> This organization is active.
          </p>
        ) : (
          <div className="space-y-3">
            {!d.can_activate && reasons.length > 0 ? (
              <div role="group" aria-labelledby="blockers-title" className="text-sm">
                <p id="blockers-title" className="font-medium text-ink">
                  Activation is not available yet:
                </p>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-ink-muted">
                  {reasons.map((r) => {
                    const key = remediationKeyFor(r);
                    const href = key ? remediationHref(key) : null;
                    return (
                      <li key={r}>
                        {r}
                        {href ? (
                          <>
                            {" "}
                            <Link className="inline-flex min-h-11 items-center text-brand-700 underline" href={href}>
                              Fix this
                            </Link>
                          </>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : null}
            <Button className="min-h-11" isDisabled={!d.can_activate} pending={activate.isPending} onPress={() => activate.mutate(undefined)} aria-describedby={!d.can_activate ? "blockers-title" : undefined}>
              Activate organization
            </Button>
            <p className="text-xs text-ink-muted">There is no payment, plan or override shortcut. The server decides from the facts above.</p>
            {activate.error && !refusal ? <ApiErrorView error={activate.error} title="Activation did not complete" /> : null}
            {refusal ? <Badge tone="warn">Refused by the server: {refusal.message}</Badge> : null}
          </div>
        )}
      </Card>
    </>
  );
}
