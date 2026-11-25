"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { VerifyApiResultRow, VerifyApiSession } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, Dialog, EmptyState, Select, TextField, type Column } from "@zoiko/ui";
import { ClipboardList } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { checkLabel, cleanReference, ENVIRONMENT_FILTERS, historyLines, outcomeLabel, STATUS_FILTERS } from "./results-logic";
import { sessionState } from "./test-lab-logic";

const KEY = ["verify-api", "results"];
const when = (iso: string | null | undefined): string => (iso ? new Date(iso).toLocaleString("en-GB") : "-");

/** Every verification your applications have started (CR-155): search by application, state or your customer's reference, open one to see how it was decided, and start a recheck. You see only the result:
 * which checks passed and why, never a photograph, a document or a score. */
export function ResultsCard() {
  const [app, setApp] = useState("");
  const [environment, setEnvironment] = useState("");
  const [status, setStatus] = useState("");
  const [reference, setReference] = useState("");
  const [cursors, setCursors] = useState<string[]>([]); // the pages already passed, so Back works
  const [open, setOpen] = useState<string | null>(null);
  const cursor = cursors[cursors.length - 1];
  const apps = useCellQuery(["verify-api", "applications"], (api) => api.verifyApi.applications());
  const query = { application_id: app || undefined, environment: environment || undefined, status: status || undefined, customer_reference: cleanReference(reference), cursor, limit: 10 };
  const q = useCellQuery([...KEY, query], (api) => api.verifyApi.results(query));
  const reset = () => setCursors([]);
  const columns: Column<VerifyApiResultRow>[] = [
    { key: "r", header: "Your reference", pinned: true, render: (s) => <span className="font-mono text-xs">{s.customer_reference}</span> },
    { key: "a", header: "Application", render: (s) => s.application_name },
    { key: "e", header: "Where", render: (s) => <Badge tone={s.mode === "sandbox" ? "info" : "warn"}>{s.mode === "sandbox" ? "Sandbox test" : "Live"}</Badge> },
    { key: "s", header: "State", render: (s) => <Badge tone={sessionState(s.status).tone}>{sessionState(s.status).label}</Badge> },
    { key: "f", header: "Started", render: (s) => when(s.created_at) },
    { key: "d", header: "Finished", render: (s) => when(s.completed_at) },
  ];
  return (
    <Card title="Results" description="Search the verifications your applications started and open one to see how it was decided." padded={false}>
      <div className="space-y-3 p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Select label="Application" options={[{ id: "", label: "All applications" }, ...(apps.data?.items ?? []).map((a) => ({ id: a.application_id, label: a.name }))]} value={app} onChange={(v) => (setApp(v), reset())} />
          <Select label="Where" options={ENVIRONMENT_FILTERS} value={environment} onChange={(v) => (setEnvironment(v), reset())} />
          <Select label="State" options={STATUS_FILTERS} value={status} onChange={(v) => (setStatus(v), reset())} />
          <TextField label="Your reference" description="Exactly as you sent it" value={reference} onChange={(v) => (setReference(v), reset())} />
        </div>
        <QueryView query={q}>
          {(page) => (
            <>
              <DataTable<VerifyApiResultRow>
                caption="Verifications"
                columns={columns}
                rows={page.items}
                rowKey={(s) => s.session_id}
                onRowAction={(s) => setOpen(s.session_id)}
                empty={
                  <EmptyState title="No verifications found" icon={ClipboardList} compact>
                    Change the filters, or make a test verification in an application&apos;s Test Lab.
                  </EmptyState>
                }
              />
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="secondary" isDisabled={cursors.length === 0} onPress={() => setCursors(cursors.slice(0, -1))}>
                  Newer
                </Button>
                <Button size="sm" variant="secondary" isDisabled={!page.next_cursor} onPress={() => page.next_cursor && setCursors([...cursors, page.next_cursor])}>
                  Older
                </Button>
              </div>
            </>
          )}
        </QueryView>
      </div>
      {open ? <ResultDialog id={open} onClose={() => setOpen(null)} onOpenOther={setOpen} /> : null}
    </Card>
  );
}

function ResultDialog({ id, onClose, onOpenOther }: { id: string; onClose: () => void; onOpenOther: (id: string) => void }) {
  const qc = useQueryClient();
  const q = useCellQuery([...KEY, "one", id], (api) => api.verifyApi.result(id));
  const [made, setMade] = useState<VerifyApiSession | null>(null);
  const recheck = useCellMutation((api) => api.verifyApi.recheck(id), {
    onSuccess: (s) => {
      setMade(s);
      void qc.invalidateQueries({ queryKey: KEY });
    },
  });
  return (
    <Dialog isOpen onOpenChange={(o) => !o && onClose()} title="Verification result" description="Only the result is shown: never a photograph, a document or a score." size="lg" footer={<Button onPress={onClose}>Close</Button>}>
      <QueryView query={q}>
        {(d) => (
          <div className="space-y-4">
            <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
              <div><dt className="text-xs text-ink-muted">Your reference</dt><dd className="font-mono">{d.customer_reference}</dd></div>
              <div><dt className="text-xs text-ink-muted">Application</dt><dd>{d.application_name}</dd></div>
              <div><dt className="text-xs text-ink-muted">State</dt><dd>{sessionState(d.status).label}</dd></div>
              <div><dt className="text-xs text-ink-muted">Started</dt><dd>{when(d.created_at)}</dd></div>
              {d.recheck_of ? (
                <div className="sm:col-span-2">
                  <dt className="text-xs text-ink-muted">This is a recheck of</dt>
                  <dd>
                    <Button size="xs" variant="secondary" onPress={() => onOpenOther(d.recheck_of!)}>
                      Open the earlier check
                    </Button>
                  </dd>
                </div>
              ) : null}
            </dl>
            <section aria-label="Decisions" className="space-y-2">
              <h3 className="text-sm font-semibold text-ink">How it was decided</h3>
              {d.decisions.length === 0 ? (
                <p className="text-sm text-ink-muted">No decision yet.</p>
              ) : (
                <>
                  <ul className="space-y-1 text-sm">
                    {historyLines(d.decisions).map((l) => (
                      <li key={l.id} className={l.current ? "font-medium text-ink" : "text-ink-muted"}>
                        {l.text}
                      </li>
                    ))}
                  </ul>
                  {(() => {
                    const latest = [...d.decisions].sort((a, b) => b.decision_version - a.decision_version)[0]!;
                    const o = outcomeLabel(latest.outcome);
                    return (
                      <div className="space-y-1 rounded-lg border border-border p-3 text-sm">
                        <div className="flex items-center gap-2">
                          <Badge tone={o.tone}>{o.label}</Badge>
                          {latest.sandbox ? <Badge tone="info">Sandbox test</Badge> : null}
                          <span className="text-xs text-ink-muted">{when(latest.completed_at)}</span>
                        </div>
                        <ul className="text-xs text-ink-muted">
                          {latest.checks.map((c) => (
                            <li key={c.method}>
                              {checkLabel(c.method)}: {c.result}
                            </li>
                          ))}
                        </ul>
                        {latest.reason_codes.length ? <p className="text-xs text-ink-muted">Reasons: {latest.reason_codes.join(", ")}</p> : null}
                      </div>
                    );
                  })()}
                </>
              )}
            </section>
            {d.rechecks.length ? (
              <section aria-label="Rechecks" className="space-y-1">
                <h3 className="text-sm font-semibold text-ink">Rechecks</h3>
                <ul className="space-y-1 text-sm">
                  {d.rechecks.map((r) => (
                    <li key={r.session_id} className="flex items-center gap-2">
                      <Badge tone={sessionState(r.status).tone}>{sessionState(r.status).label}</Badge>
                      <span className="text-xs text-ink-muted">{when(r.created_at)}</span>
                      <Button size="xs" variant="secondary" onPress={() => onOpenOther(r.session_id)}>
                        Open
                      </Button>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {made?.launch_url ? (
              <Banner tone="info" title="Send this link to your customer">
                <a className="break-all text-brand-600 underline" href={made.launch_url} target="_blank" rel="noreferrer noopener">
                  {made.launch_url}
                </a>{" "}
                It is shown only now and stops working after 30 minutes or when the check is finished.
              </Banner>
            ) : null}
            <div className="flex items-center gap-3">
              <Button size="sm" isDisabled={!d.can_recheck || recheck.isPending} pending={recheck.isPending} onPress={() => recheck.mutate(undefined)}>
                Start a recheck
              </Button>
              {!d.can_recheck ? <span className="text-xs text-ink-muted">A recheck can start once this verification has ended.</span> : null}
            </div>
            {recheck.error ? <ApiErrorView error={recheck.error} title="The recheck was not started" onRetry={() => recheck.reset()} /> : null}
          </div>
        )}
      </QueryView>
    </Dialog>
  );
}
