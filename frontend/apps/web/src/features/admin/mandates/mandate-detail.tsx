"use client";

import type { LedgerAmounts, Mandate, MandateBalance } from "@zoiko/api-client";
import { Badge, Button, CopyId, DateTime, Divider, KeyValues, Meter, RelativeTime, SidePanel, StaleNotice, StatusBadge, humanize } from "@zoiko/ui";
import type { ReactNode } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { ScopeList } from "../authority/shared";
import { MANDATES_KEY, allowedMoves, type Move } from "./mandate-status";
import { ReservationsPanel } from "./reservations";

export function MandateDetail({ mandate: m, onClose, onMove }: { mandate: Mandate; onClose: () => void; onMove: (mv: Move) => void }) {
  const moves = allowedMoves(m.status);
  return (
    <SidePanel
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title="Mandate"
      subtitle={
        <div className="flex flex-wrap items-center gap-1.5">
          <CopyId value={m.mandate_id} label="mandate" />
          <StatusBadge state={m.status} kind="Mandate" size="xs" />
        </div>
      }
      footer={
        moves.length
          ? moves.map((mv) => (
              <Button key={mv} variant={mv === "revoke" ? "danger-outline" : "secondary"} onPress={() => onMove(mv)}>
                {humanize(mv)}
              </Button>
            ))
          : undefined
      }
    >
      <div className="space-y-5">
        {m.status === "PENDING_APPROVAL" ? (
          <p className="rounded-lg border border-info-fg/20 bg-info-bg px-3 py-2 text-sm text-ink-muted">
            New mandates start pending. Activate it once it has been reviewed; until then the executor cannot act under it.
          </p>
        ) : null}
        <BalanceView mandateId={m.mandate_id} mandate={m} />
        <Divider />
        <KeyValues
          items={[
            ["Executor", <CopyId key="ex" value={m.executor_principal_id} label="executor" />],
            ["On behalf of", <CopyId key="rp" value={m.represented_principal_id} label="represented principal" />],
            ["Source grant", <CopyId key="g" value={m.authority_grant_id} label="source grant" />],
            ["Purpose", <CopyId key="p" value={m.purpose_id} label="purpose" />],
            ["Valid from", <DateTime key="vf" value={m.valid_from} />],
            ["Valid until", <DateTime key="vt" value={m.valid_to} />],
            ["Value limit", m.value_limit !== null ? <Amount key="vl" value={m.value_limit} currency={m.currency} /> : "None"],
            ["Per transaction", m.per_transaction_value_limit !== null ? <Amount key="pt" value={m.per_transaction_value_limit} currency={m.currency} /> : "None"],
            ["Count limit", m.count_limit ?? "None"],
            ["Data limit", m.data_limit_bytes !== null ? `${m.data_limit_bytes} bytes` : "None"],
            ["Delegation", m.delegation_allowed ? <Badge key="d" tone="info">{`Allowed, depth ≤ ${m.max_delegation_depth}`}</Badge> : "Not allowed"],
            ["Parent mandate", m.parent_mandate_id ? <CopyId key="pm" value={m.parent_mandate_id} label="parent mandate" /> : "—"],
          ]}
        />
        <Terms label="Allowed actions" values={m.allowed_actions} />
        <Terms label="Resource scopes" values={m.resource_scopes} />
        <Terms label="Tools" values={m.tools} empty="Any tool" />
        <Terms label="Jurisdictions" values={m.jurisdictions} empty="Any jurisdiction" />
        <Divider />
        <ReservationsPanel mandate={m} />
      </div>
    </SidePanel>
  );
}

function Terms({ label, values, empty }: { label: string; values: string[]; empty?: string }) {
  return (
    <div>
      <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">{label}</h3>
      <div className="mt-1">{values.length === 0 && empty ? <span className="text-sm italic text-ink-muted">{empty}</span> : <ScopeList values={values} />}</div>
    </div>
  );
}

function Amount({ value, currency }: { value: string | number | null | undefined; currency?: string | null }) {
  if (value === null || value === undefined) return <span className="text-ink-subtle">—</span>;
  return <span className="tabular font-mono">{`${value}${currency ? ` ${currency}` : ""}`}</span>;
}

/** Live balance from the ledger, refreshed every 10 s while the panel is open. */
export function BalanceView({ mandateId, mandate }: { mandateId: string; mandate: Mandate }) {
  const balance = useCellQuery([...MANDATES_KEY, "balance", mandateId], (api) => api.mandates.balance(mandateId), { refetchInterval: 10_000 });
  return (
    <section aria-labelledby="balance-heading" className="space-y-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 id="balance-heading" className="text-sm font-semibold text-ink">
          Live balance
        </h3>
        {balance.data ? <span className="text-xs text-ink-subtle">Ledger version {balance.data.ledger_version} · refreshes every 10 s</span> : null}
      </div>
      {balance.isError && balance.data ? (
        <>
          <StaleNotice age={<RelativeTime value={new Date(balance.dataUpdatedAt).toISOString()} />} onRefresh={() => void balance.refetch()} />
          <BalanceBars b={balance.data} mandate={mandate} />
        </>
      ) : (
        <QueryView query={balance} loadingRows={2}>
          {(b) => <BalanceBars b={b} mandate={mandate} />}
        </QueryView>
      )}
    </section>
  );
}

function BalanceBars({ b, mandate }: { b: MandateBalance; mandate: Mandate }) {
  const rows: ReactNode[] = [];
  const bar = (key: keyof LedgerAmounts, label: string, limit: string | number | null, unit: string) => {
    if (limit === null) return;
    const lim = Number(limit);
    const committed = Number(b.committed[key] ?? 0);
    const reserved = Number(b.reserved[key] ?? 0);
    const used = committed + reserved;
    const remaining = b.remaining[key];
    const tone = lim > 0 && used / lim >= 0.9 ? "danger" : lim > 0 && used / lim >= 0.7 ? "warn" : "brand";
    rows.push(
      <div key={key} className="space-y-1">
        <Meter
          label={label}
          value={used}
          max={lim || 1}
          tone={tone}
          valueText={`${remaining ?? "—"} ${unit} remaining of ${limit} ${unit}`.replace(/\s+/g, " ")}
        />
        <p className="text-xs text-ink-muted">
          Committed <span className="tabular font-mono text-ink">{String(b.committed[key] ?? 0)}</span> · Reserved{" "}
          <span className="tabular font-mono text-ink">{String(b.reserved[key] ?? 0)}</span>
        </p>
      </div>,
    );
  };
  bar("value", "Value", mandate.value_limit, b.currency ?? mandate.currency ?? "");
  bar("count", "Uses", mandate.count_limit, "");
  bar("data_bytes", "Data", mandate.data_limit_bytes, "bytes");
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm">
        Ledger status <StatusBadge state={b.status} kind="Mandate" size="xs" />
      </div>
      {rows.length ? rows : <p className="text-sm text-ink-muted">This mandate has no budget limits; the ledger records usage only.</p>}
    </div>
  );
}
