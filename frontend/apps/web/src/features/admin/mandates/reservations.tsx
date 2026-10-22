"use client";

import type { CommitBody, Mandate, Reservation, ReserveBody } from "@zoiko/api-client";
import { Banner, Button, Checkbox, CopyId, DataTable, DateTime, Dialog, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { FlaskConical } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, newIdempotencyKey, useCellMutation } from "@/lib/cell";

import { DECIMAL_RE, FormProblem, intInput, useIdempotency } from "../authority/shared";
import { MANDATES_KEY } from "./mandate-status";

/**
 * Operator / testing tool: drives the mandate ledger the way a PEP would (reserve → commit or
 * release). Reservations made here consume real budget. The cell has no reservation list, so this
 * panel only remembers the reservations made in this browser tab.
 */
export function ReservationsPanel({ mandate: m }: { mandate: Mandate }) {
  const [made, setMade] = useState<Reservation[]>([]);
  const [committing, setCommitting] = useState<Reservation | null>(null);
  const upsert = (r: Reservation) => setMade((xs) => [r, ...xs.filter((x) => x.reservation_id !== r.reservation_id)]);

  const release = useCellMutation((api, id: string) => api.mandates.release(id), {
    invalidate: [[...MANDATES_KEY, "balance", m.mandate_id]],
    success: "Reservation released",
    onSuccess: (r, id) => upsert({ ...r, reservation_id: r.reservation_id ?? id }),
  });

  const columns: Column<Reservation>[] = [
    { key: "id", header: "Reservation", pinned: true, render: (r) => <CopyId value={r.reservation_id} label="reservation" /> },
    { key: "status", header: "Status", render: (r) => <StatusBadge state={r.status} kind="Reservation" size="xs" /> },
    {
      key: "amounts",
      header: "Amounts",
      render: (r) => (
        <span className="tabular font-mono text-xs">
          {[r.amounts.value != null && `${r.amounts.value} ${m.currency ?? ""}`.trim(), r.amounts.count ? `${r.amounts.count} uses` : null, r.amounts.data_bytes ? `${r.amounts.data_bytes} B` : null]
            .filter(Boolean)
            .join(" · ") || "—"}
        </span>
      ),
    },
    { key: "expires", header: "Expires", render: (r) => <DateTime value={r.expires_at} style="short" /> },
    {
      key: "row-actions",
      header: "Row actions",
      align: "end",
      render: (r) =>
        r.status === "RESERVED" ? (
          <div className="flex justify-end gap-1">
            <Button size="xs" variant="secondary" onPress={() => setCommitting(r)}>
              Commit
            </Button>
            <Button size="xs" variant="ghost" pending={release.isPending && release.variables === r.reservation_id} onPress={() => release.mutate(r.reservation_id)}>
              Release
            </Button>
          </div>
        ) : null,
    },
  ];

  return (
    <section aria-labelledby="reservations-heading" className="space-y-4">
      <div>
        <h3 id="reservations-heading" className="flex items-center gap-2 text-sm font-semibold text-ink">
          <FlaskConical aria-hidden className="size-4 text-ink-subtle" />
          Reservations test panel
        </h3>
        <Banner tone="warn" title="Operator and testing tool" className="mt-2">
          This drives the ledger as an enforcement point would. Reservations here hold and commit real budget on this mandate. Use it to
          check limits, not to perform business actions.
        </Banner>
      </div>
      {m.status === "ACTIVE" ? <ReserveForm mandate={m} onReserved={upsert} /> : <p className="text-sm text-ink-muted">Only an active mandate can reserve budget.</p>}
      {release.error ? <ApiErrorView error={release.error} title="The reservation was not released" /> : null}
      {made.length ? (
        <DataTable caption="Reservations made in this session" columns={columns} rows={made} rowKey={(r) => r.reservation_id} dense />
      ) : null}
      {committing ? (
        <CommitDialog
          reservation={committing}
          mandateId={m.mandate_id}
          onClose={() => setCommitting(null)}
          onDone={(r) => {
            upsert(r);
            setCommitting(null);
          }}
        />
      ) : null}
    </section>
  );
}

function ReserveForm({ mandate: m, onReserved }: { mandate: Mandate; onReserved: (r: Reservation) => void }) {
  const [action, setAction] = useState(m.allowed_actions[0] ?? "");
  const [tool, setTool] = useState("");
  const [tc, setTc] = useState(() => `test|mandate:${m.mandate_id}|${new Date().toISOString()}`);
  const [value, setValue] = useState("");
  const [count, setCount] = useState("");
  const [bytes, setBytes] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const keyFor = useIdempotency(newIdempotencyKey);

  const reserve = useCellMutation((api, v: { body: ReserveBody; key: string }) => api.mandates.reserve(m.mandate_id, v.body, v.key), {
    invalidate: [[...MANDATES_KEY, "balance", m.mandate_id]],
    success: "Budget reserved",
    onSuccess: (r) => onReserved(r),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!action.trim()) return setProblem("Choose an action.");
    if (!tc.trim()) return setProblem("Enter a transaction context.");
    const c = intInput(count);
    const d = intInput(bytes);
    if (Number.isNaN(c) || Number.isNaN(d)) return setProblem("Count and data must be whole numbers.");
    if (value.trim() && !DECIMAL_RE.test(value.trim())) return setProblem("Value must be a non-negative decimal, e.g. 12.50.");
    if (value.trim() && !m.currency) return setProblem("This mandate has no currency, so it cannot reserve value.");
    setProblem(null);
    const body: ReserveBody = {
      action: action.trim(),
      tool: tool.trim() || null,
      transaction_context: tc,
      value: value.trim() && m.currency ? { amount: value.trim(), currency: m.currency } : null,
      count: c ?? 0,
      data_bytes: d ?? 0,
    };
    reserve.mutate({ body, key: keyFor(body) });
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-dashed border-border-strong p-4" aria-label="Reserve budget">
      <div className="grid gap-3 sm:grid-cols-2">
        {m.allowed_actions.length ? (
          <Select label="Action" isRequired value={action} onChange={setAction} options={m.allowed_actions.map((a) => ({ id: a, label: a }))} />
        ) : (
          <TextField label="Action" isRequired value={action} onChange={setAction} mono />
        )}
        {m.tools.length ? (
          <Select
            label="Tool"
            value={tool || "__none__"}
            onChange={(v) => setTool(v === "__none__" ? "" : v)}
            options={[{ id: "__none__", label: "No tool" }, ...m.tools.map((t) => ({ id: t, label: t }))]}
          />
        ) : (
          <TextField label="Tool (optional)" value={tool} onChange={setTool} mono />
        )}
        <TextField label={`Value${m.currency ? ` (${m.currency})` : ""}`} value={value} onChange={setValue} inputMode="decimal" mono placeholder="0.00" isDisabled={!m.currency} />
        <div className="grid grid-cols-2 gap-3">
          <TextField label="Count" value={count} onChange={setCount} inputMode="numeric" mono placeholder="0" />
          <TextField label="Data (bytes)" value={bytes} onChange={setBytes} inputMode="numeric" mono placeholder="0" />
        </div>
      </div>
      <TextField
        label="Transaction context"
        isRequired
        value={tc}
        onChange={setTc}
        mono
        description="Identifies the transaction the reservation is for. Stored only as a hash."
      />
      <FormProblem>{problem}</FormProblem>
      {reserve.error ? <ApiErrorView error={reserve.error} title="Nothing was reserved" /> : null}
      <div className="flex justify-end">
        <Button type="submit" variant="secondary" pending={reserve.isPending}>
          Reserve
        </Button>
      </div>
    </form>
  );
}

function CommitDialog({ reservation, mandateId, onClose, onDone }: { reservation: Reservation; mandateId: string; onClose: () => void; onDone: (r: Reservation) => void }) {
  const [recheck, setRecheck] = useState(true);
  const [executionId, setExecutionId] = useState("");
  const commit = useCellMutation((api, body: CommitBody) => api.mandates.commit(reservation.reservation_id, body), {
    invalidate: [[...MANDATES_KEY, "balance", mandateId]],
    success: (r) => (r.status === "COMMITTED" ? "Reservation committed" : `Reservation ${r.status.toLowerCase().replace(/_/g, " ")}`),
    onSuccess: (r) => onDone({ ...r, reservation_id: r.reservation_id ?? reservation.reservation_id }),
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !commit.isPending && onClose()}
      title="Commit reservation"
      size="sm"
      footer={
        <>
          <Button variant="secondary" isDisabled={commit.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={commit.isPending} onPress={() => commit.mutate({ recheck_passed: recheck, execution_id: executionId.trim() || null })}>
            Commit
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm">
        <p className="text-ink-muted">
          Committing turns reservation <CopyId value={reservation.reservation_id} label="reservation" /> into spent budget.
        </p>
        <Checkbox isSelected={recheck} onChange={setRecheck} description="The enforcement point's commit-time recheck result. Unticked, the cell refuses to commit.">
          Commit-time recheck passed
        </Checkbox>
        <TextField label="Execution ID (optional)" value={executionId} onChange={setExecutionId} mono description="The downstream execution this spend belongs to." />
        {commit.error ? <ApiErrorView error={commit.error} title="The reservation was not committed" /> : null}
      </div>
    </Dialog>
  );
}
