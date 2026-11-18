"use client";

import type { ScreeningGateModule, ScreeningGateStatus } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DateTime, PageHeader, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { Mono, RegError } from "../regulatory/common";

import { blockerText, GATE_MEANING, GATES, headline, MODULES, reasonProblem, signProblem, validMarket } from "./gate-logic";

function Gate({ market, module, refresh }: { market: string; module: string; refresh: { invalidate: never[] } }) {
  const [gate, setGate] = useState<string>("G0");
  const [evidence, setEvidence] = useState("");
  const [days, setDays] = useState("180");
  const sign = useCellMutation(
    (api, v: { gate: string; evidence: string; days: number }) => api.regulatory.screeningGateSign(market, module, { gate: v.gate, evidence_ref: v.evidence.trim(), valid_days: v.days }),
    { ...refresh, success: "Signed", onSuccess: () => setEvidence("") },
  );
  const problem = signProblem(evidence, Number(days));
  return (
    <Card title="Sign a gate" description="You sign in the one role your capability names. A role signs each gate once, and a signature expires.">
      <div className="space-y-3 text-sm">
        <Select label="Gate" value={gate} onChange={setGate} options={GATES.map((g) => ({ id: g, label: `${g}: ${GATE_MEANING[g]}` }))} />
        <TextField label="Evidence reference (a document or record you checked)" value={evidence} onChange={setEvidence} />
        <TextField label="Valid for (days, at most 365)" value={days} onChange={setDays} />
        {evidence && problem ? <p className="text-danger">{problem}</p> : null}
        <Button pending={sign.isPending} isDisabled={problem !== null} onPress={() => sign.mutate({ gate, evidence, days: Number(days) })}>
          Sign {gate}
        </Button>
        {sign.error ? <RegError error={sign.error} /> : null}
      </div>
    </Card>
  );
}

function Records({ s, refresh }: { s: ScreeningGateStatus; refresh: { invalidate: never[] } }) {
  const close = useCellMutation<string, unknown>((api, id) => api.regulatory.screeningGateClose(id), { ...refresh, success: "Defect closed" });
  const release = useCellMutation<string, unknown>((api, id) => api.regulatory.screeningGateRelease(id), { ...refresh, success: "Hold released" });
  return (
    <Card title="Open defects and holds" description="A hold is released by someone other than the person who placed it.">
      <div className="space-y-3 text-sm">
        {s.open_defects.length === 0 && s.holds.length === 0 ? <p className="text-ink-muted">None.</p> : null}
        <ul className="space-y-2">
          {s.open_defects.map((d) => (
            <li key={d.record_id} className="flex flex-wrap items-center gap-2">
              <Badge tone="danger">defect</Badge> {d.ref} <DateTime value={d.opened_at} />
              <Button variant="secondary" pending={close.isPending} onPress={() => close.mutate(d.record_id)}>Close</Button>
            </li>
          ))}
          {s.holds.map((h) => (
            <li key={h.record_id} className="flex flex-wrap items-center gap-2">
              <Badge tone="warn">hold</Badge> {h.reason} <DateTime value={h.placed_at} />
              <Button variant="secondary" pending={release.isPending} onPress={() => release.mutate(h.record_id)}>Release (not the person who placed it)</Button>
            </li>
          ))}
        </ul>
        {close.error ? <RegError error={close.error} /> : null}
        {release.error ? <RegError error={release.error} /> : null}
      </div>
    </Card>
  );
}

function Raise({ market, module, refresh }: { market: string; module: string; refresh: { invalidate: never[] } }) {
  const [text, setText] = useState("");
  const defect = useCellMutation((api, ref: string) => api.regulatory.screeningGateDefect(market, module, ref), { ...refresh, success: "Defect opened: every gate is stopped", onSuccess: () => setText("") });
  const hold = useCellMutation((api, ref: string) => api.regulatory.screeningGateHold(market, module, ref), { ...refresh, success: "Hold placed: the module is stopped", onSuccess: () => setText("") });
  const problem = text ? reasonProblem(text) : null;
  return (
    <Card title="Stop this module" description="A critical defect or a kill switch takes effect at once. Use a hold for an incident, a defect for something that must be fixed.">
      <div className="space-y-3 text-sm">
        <TextField label="What is wrong (reference or short reason)" value={text} onChange={setText} />
        {problem ? <p className="text-danger">{problem}</p> : null}
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" pending={defect.isPending} isDisabled={reasonProblem(text) !== null} onPress={() => defect.mutate(text.trim())}>Open a critical defect</Button>
          <Button variant="secondary" pending={hold.isPending} isDisabled={reasonProblem(text) !== null} onPress={() => hold.mutate(text.trim())}>Place a hold</Button>
        </div>
        {defect.error ? <RegError error={defect.error} /> : null}
        {hold.error ? <RegError error={hold.error} /> : null}
      </div>
    </Card>
  );
}

export function ScreeningGatesPage() {
  const [market, setMarket] = useState("");
  const [module, setModule] = useState<ScreeningGateModule>("SANCTIONS");
  const ok = validMarket(market);
  const code = market.trim().toUpperCase();
  const key = ["screening", "gates", code, module] as const;
  const q = useCellQuery([...key], (api) => api.regulatory.screeningGate(code, module), { enabled: ok });
  const refresh = { invalidate: [[...key]] as never[] };
  return (
    <>
      <PageHeader
        title="Release gates"
        description="A screening module runs in a market only when every gate G0 to G3 is signed by its named roles, no critical defect is open and no hold is in place. Nothing here signs for anyone."
        breadcrumbs={[{ label: "Screening" }, { label: "Release gates" }]}
      />
      <div className="space-y-4">
        <Card title="Which module, in which market">
          <div className="flex flex-wrap items-end gap-3">
            <TextField label="Market (two letters)" value={market} onChange={setMarket} />
            <Select label="Module" value={module} onChange={(v) => setModule(v as ScreeningGateModule)} options={MODULES.map((m) => ({ id: m.id, label: m.label }))} />
          </div>
        </Card>
        {ok ? (
          <QueryView query={q}>
            {(s) => (
              <>
                <Banner tone={headline(s).tone} title={headline(s).text}>
                  <Mono>{s.market}</Mono> / {s.module}
                </Banner>
                {s.blockers.length ? <ul className="list-disc space-y-1 pl-5 text-sm">{s.blockers.map((b) => <li key={b}>{blockerText(b)}</li>)}</ul> : null}
                <Card title="Who still has to sign" description="Each gate needs every role named for it.">
                  <ul className="space-y-1 text-sm">
                    {GATES.map((g) => (
                      <li key={g}>
                        <Badge tone={(s.missing[g] ?? []).length ? "warn" : "ok"}>{g}</Badge> {GATE_MEANING[g]}: {(s.missing[g] ?? []).length ? `waiting for ${(s.missing[g] ?? []).join(", ")}` : "signed"}
                      </li>
                    ))}
                  </ul>
                </Card>
                <Gate market={code} module={module} refresh={refresh} />
                <Raise market={code} module={module} refresh={refresh} />
                <Records s={s} refresh={refresh} />
              </>
            )}
          </QueryView>
        ) : (
          <p className="text-sm text-ink-muted">Enter a market to see its gates.</p>
        )}
      </div>
    </>
  );
}
