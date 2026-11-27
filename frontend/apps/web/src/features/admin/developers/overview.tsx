"use client";

import { Badge, Banner, Button, Card, DataTable, EmptyState, Stat, type Column } from "@zoiko/ui";
import type { VerifyApiResultRow } from "@zoiko/api-client";
import { ShieldCheck } from "lucide-react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { attention, isWaiting, percent, successRate } from "./overview-logic";
import { outcomeLabel } from "./results-logic";
import { ENDPOINTS_KEY } from "./shared";
import { sessionState } from "./test-lab-logic";

const when = (iso: string | null | undefined): string => (iso ? new Date(iso).toLocaleString("en-GB") : "-");
const REFRESH_MS = 15000;

/** The first thing a person sees under Identity verification: how many checks ran this month and how they ended, what needs attention, and the latest verifications. Everything here is read from the platform
 * and refreshes by itself. Sandbox tests are counted apart and never mixed into the live numbers. */
export function OverviewSection({ onGo }: { onGo: (tab: string) => void }) {
  const usage = useCellQuery(["verify-api", "overview", "usage"], (api) => api.verifyApi.usage(), { refetchInterval: REFRESH_MS });
  const recent = useCellQuery(["verify-api", "overview", "recent"], (api) => api.verifyApi.results({ limit: 8 }), { refetchInterval: REFRESH_MS });
  const live = useCellQuery(["verify-api", "overview", "go-live"], (api) => api.verifyApi.goLive(), { refetchInterval: REFRESH_MS * 4 });
  const hooks = useCellQuery([...ENDPOINTS_KEY, "overview"], (api) => api.webhooks.endpoints(), { refetchInterval: REFRESH_MS * 2 });

  const columns: Column<VerifyApiResultRow>[] = [
    { key: "r", header: "Your reference", pinned: true, render: (s) => <span className="font-mono text-xs">{s.customer_reference}</span> },
    { key: "c", header: "Country", render: (s) => s.country },
    { key: "m", header: "Where", render: (s) => <Badge tone={s.mode === "sandbox" ? "info" : "warn"}>{s.mode === "sandbox" ? "Test" : "Live"}</Badge> },
    { key: "s", header: "State", render: (s) => <Badge tone={sessionState(s.status).tone}>{sessionState(s.status).label}</Badge> },
    { key: "o", header: "Result", render: (s) => (s.outcome ? <Badge tone={outcomeLabel(s.outcome).tone}>{outcomeLabel(s.outcome).label}</Badge> : <span className="text-xs text-ink-muted">Not decided</span>) },
    { key: "t", header: "Started", render: (s) => when(s.created_at) },
  ];

  return (
    <div className="space-y-5">
      <QueryView query={usage} loadingRows={2}>
        {(u) => {
          const p = u.totals.production;
          const rate = successRate(p);
          const items = recent.data?.items ?? [];
          const waiting = items.filter((s) => s.mode === "production" && isWaiting(s.status)).length;
          const notes = attention({
            productionOpen: live.data?.grant?.status === "ACTIVE",
            activeWebhooks: (hooks.data?.items ?? []).filter((e) => e.status === "ACTIVE").length,
            waiting,
            inconclusive: p.inconclusive,
            verified: p.verified,
          });
          return (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                <Stat label="Started this month" value={p.sessions_started} hint="Live verifications" />
                <Stat label="Verified" value={p.verified} hint="Ended verified" />
                <Stat label="Not verified" value={p.not_verified} hint="Ended refused" />
                <Stat label="No answer" value={p.inconclusive} hint="Left or ran out of tries" />
                <Stat label="Success rate" value={percent(rate)} hint="Of the verifications that ended" />
              </div>
              {u.totals.sandbox.sessions_started > 0 ? (
                <p className="text-xs text-ink-muted">Not counted above: {u.totals.sandbox.sessions_started} sandbox tests this month.</p>
              ) : null}
              {notes.length ? (
                <div className="space-y-2">
                  {notes.map((n) => (
                    <Banner key={n.text} tone={n.tone === "warn" ? "warn" : n.tone === "ok" ? "ok" : "info"} title={n.text}>
                      {n.go ? (
                        <Button size="xs" variant="secondary" onPress={() => onGo(n.go!.tab)}>
                          {n.go.label}
                        </Button>
                      ) : null}
                    </Banner>
                  ))}
                </div>
              ) : null}
            </>
          );
        }}
      </QueryView>

      <Card
        title="Latest verifications"
        description="The newest first. This list refreshes by itself."
        actions={
          <Button size="sm" variant="secondary" onPress={() => onGo("results")}>
            See all and open one
          </Button>
        }
        padded={false}
      >
        <QueryView query={recent} loadingRows={3}>
          {(d) => (
            <DataTable<VerifyApiResultRow>
              caption="Latest verifications"
              columns={columns}
              rows={d.items}
              rowKey={(s) => s.session_id}
              empty={
                <EmptyState title="No verifications yet" icon={ShieldCheck} compact>
                  Start one from your project, or run a test in the Test tab.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>
    </div>
  );
}
