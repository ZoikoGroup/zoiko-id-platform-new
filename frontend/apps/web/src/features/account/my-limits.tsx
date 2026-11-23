"use client";

import type { MyMandate } from "@zoiko/api-client";
import { Badge, Button, Card, DateTime, EmptyState, PageHeader, StatusBadge, humanize } from "@zoiko/ui";
import { Gauge } from "lucide-react";
import { useState } from "react";

import { BalanceView } from "@/features/admin/mandates/mandate-detail";
import { MANDATES_KEY, StatusChangeDialog, budgetSummary } from "@/features/admin/mandates/mandate-status";
import { QueryView, useCellQuery } from "@/lib/cell";

import { MyPermissions } from "./my-permissions-card";
import { canUseLimit } from "./use-limit";
import { UseLimitDialog } from "./use-limit-dialog";

const KEY = [...MANDATES_KEY, "mine"];

/** What the person is to this limit, in words they would use. */
function roleWords(m: MyMandate): string {
  const runs = m.i_am.includes("executor");
  const made = m.i_am.includes("creator");
  if (runs && made) return "You set this up and you use it";
  if (made) return "You set this up for someone, or something, else";
  return "You were given this to use";
}

/**
 * "My limits": the spending and action limits that concern the signed-in person, whatever their role. A Member sees
 * only their own; owners and administrators see everyone's under Mandates. Pause and Revoke appear only when the cell
 * says they are open to this person right now (`my_moves`), so no button is offered that would be refused.
 */
export function MyLimitsPage() {
  const mine = useCellQuery(KEY, (api) => api.mandates.mine());
  const [move, setMove] = useState<{ mandate: MyMandate; move: "pause" | "revoke" } | null>(null);
  const [using, setUse] = useState<MyMandate | null>(null);

  return (
    <>
      <PageHeader
        icon={Gauge}
        title="My limits and permissions"
        description="What you, or an agent you set up, may spend or do on the company's behalf, and how much is left. Everything here is held inside the company's own rules."
      />
      <div className="mb-4">
        <MyPermissions />
      </div>
      <QueryView query={mine}>
        {(data) =>
          data.items.length === 0 ? (
            <Card>
              <EmptyState icon={Gauge} title="You have no limits set">
                You are not currently allowed to spend or approve anything on the company's behalf. If you need to, ask an owner or administrator to give you a
                grant, then set up a limit from it.
              </EmptyState>
            </Card>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              {data.items.map((m) => (
                <Card key={m.mandate_id}>
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge state={m.status} kind="Mandate" />
                      <Badge tone="neutral">{roleWords(m)}</Badge>
                    </div>
                    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                      <dt className="text-ink-subtle">Limit</dt>
                      <dd className="tabular font-mono">{budgetSummary(m)}</dd>
                      {m.per_transaction_value_limit !== null ? (
                        <>
                          <dt className="text-ink-subtle">Each payment</dt>
                          <dd className="tabular font-mono">{`up to ${m.per_transaction_value_limit} ${m.currency ?? ""}`.trim()}</dd>
                        </>
                      ) : null}
                      <dt className="text-ink-subtle">Valid until</dt>
                      <dd>
                        <DateTime value={m.valid_to} />
                      </dd>
                      <dt className="text-ink-subtle">For</dt>
                      <dd>{m.allowed_actions.map(humanize).join(", ")}</dd>
                    </dl>
                    {m.status === "PENDING_APPROVAL" ? (
                      <p className="rounded-lg border border-info-fg/20 bg-info-bg px-3 py-2 text-sm text-ink-muted">
                        Waiting for an owner or administrator to switch it on. It cannot be used until then.
                      </p>
                    ) : null}
                    <BalanceView mandateId={m.mandate_id} mandate={m} />
                    {canUseLimit(m) || m.my_moves.length ? (
                      <div className="flex flex-wrap gap-2 border-t border-border pt-3">
                        {canUseLimit(m) ? (
                          <Button size="sm" onPress={() => setUse(m)}>
                            Use my limit
                          </Button>
                        ) : null}
                        {m.my_moves.map((mv) => (
                          <Button key={mv} size="sm" variant={mv === "revoke" ? "danger-outline" : "secondary"} onPress={() => setMove({ mandate: m, move: mv })}>
                            {mv === "pause" ? "Pause it" : "Cancel it"}
                          </Button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </Card>
              ))}
            </div>
          )
        }
      </QueryView>
      {using ? <UseLimitDialog mandate={using} onClose={() => setUse(null)} /> : null}
      {move ? <StatusChangeDialog mandate={move.mandate} move={move.move} onClose={() => setMove(null)} /> : null}
    </>
  );
}
