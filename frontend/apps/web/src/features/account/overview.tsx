"use client";

import { ButtonLink, Card, CopyId, KeyValues, MetricCard, PageHeader, RevisionChip, Skeleton, StatusBadge } from "@zoiko/ui";
import { Fingerprint, LifeBuoy, Link2, MonitorSmartphone, ScanFace, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";

import { ApiErrorView, QueryView, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

/** Count from a list query; each card shows its own loading/error state so one failing source
 * never blanks the page or reads as zero. */
function countOf<T>(q: UseQueryResult<{ items: T[] }>, filter?: (t: T) => boolean): ReactNode {
  if (q.isPending) return <Skeleton className="h-7 w-10" />;
  if (q.isError) return <span className="text-sm font-normal text-danger-fg">Unavailable</span>;
  return filter ? q.data.items.filter(filter).length : q.data.items.length;
}

export function AccountOverview() {
  const { session } = useRuntime();
  const sub = session?.authenticated ? session.subject.sub : undefined;

  const userinfo = useCellQuery(["me", "userinfo"], (api) => api.me.userinfo());
  const sessions = useCellQuery(["me", "sessions"], (api) => api.me.sessions());
  const passkeys = useCellQuery(["me", "authenticators"], (api) => api.me.authenticators());
  const consents = useCellQuery(["me", "consents"], (api) => api.me.consents());
  const state = useCellQuery(["me", "security-state", sub], (api) => api.signals.securityState("PRINCIPAL", sub!), {
    enabled: Boolean(sub),
  });

  return (
    <>
      <PageHeader title="My account" description="Your identity, sign-in methods and the apps you have connected." />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Active sessions"
          icon={MonitorSmartphone}
          href="/account/sessions"
          value={countOf(sessions, (s) => s.status === "ACTIVE")}
        />
        <MetricCard label="Passkeys" icon={Fingerprint} href="/account/passkeys" value={countOf(passkeys, (a) => a.status === "ACTIVE")} />
        <MetricCard label="Connected apps" icon={Link2} href="/account/connected-apps" value={countOf(consents)} />
        <MetricCard
          label="Security state"
          icon={ShieldCheck}
          value={
            state.isPending ? (
              <Skeleton className="h-7 w-24" />
            ) : state.isError ? (
              <span className="text-sm font-normal text-danger-fg">Unavailable</span>
            ) : (
              <StatusBadge state={state.data.state} kind="Security state" />
            )
          }
        />
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <Card title="Identity" description="As issued by the platform for this session.">
          <QueryView query={userinfo} loadingRows={2}>
            {(u) => (
              <KeyValues
                items={[
                  ["Principal ID", <CopyId key="p" value={u.sub} short={false} label="principal" />],
                  ["Tenant ID", <CopyId key="t" value={u.tenant_id} short={false} label="tenant" />],
                ]}
                columns={1}
              />
            )}
          </QueryView>
        </Card>

        <Card title="Security state" description="Continuous evaluation of your account across signals.">
          {state.isError ? (
            <ApiErrorView error={state.error} />
          ) : (
            <QueryView query={state} loadingRows={2}>
              {(s) => (
                <KeyValues
                  items={[
                    ["State", <StatusBadge key="s" state={s.state} kind="Security state" />],
                    ["Revision", <RevisionChip key="r" kind="security" value={s.revision} />],
                    ["Effective", s.effective_at ? new Date(s.effective_at).toLocaleString("en-US") : "—"],
                  ]}
                  columns={1}
                />
              )}
            </QueryView>
          )}
        </Card>

        <Card
          className="lg:col-span-2"
          title="Verification and recovery"
          description="Raise your identity assurance, and keep a way back in if you lose your passkeys."
        >
          <div className="flex flex-wrap gap-2">
            <ButtonLink href="/account/verification" icon={<ScanFace aria-hidden className="size-4" />}>
              Identity verification
            </ButtonLink>
            <ButtonLink href="/account/recovery" icon={<LifeBuoy aria-hidden className="size-4" />}>
              Recovery codes
            </ButtonLink>
          </div>
        </Card>
      </div>
    </>
  );
}
