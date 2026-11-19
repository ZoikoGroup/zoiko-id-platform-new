"use client";

import type { FaceLabResult } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, KeyValues, PageHeader } from "@zoiko/ui";
import { useEffect, useMemo, useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { REG, RegError } from "./common";

const MAX_BYTES = 8 * 1024 * 1024;

/** The photograph as base64, without the `data:` prefix, ready for the cell. */
function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("The photograph could not be read."));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.readAsDataURL(file);
  });
}

const problem = (file: File | null): string | null => {
  if (!file) return null;
  if (!["image/jpeg", "image/png"].includes(file.type)) return "Use a JPEG or PNG photograph.";
  if (file.size > MAX_BYTES) return "That photograph is larger than 8 MB.";
  return null;
};

function Picker({ label, hint, file, onFile }: { label: string; hint: string; file: File | null; onFile: (f: File | null) => void }) {
  const url = useMemo(() => (file ? URL.createObjectURL(file) : null), [file]);
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);
  const bad = problem(file);
  return (
    <div className="space-y-2">
      <div className="font-medium">{label}</div>
      <p className="text-sm text-ink-muted">{hint}</p>
      <input type="file" accept="image/jpeg,image/png" aria-label={label} onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
      {bad ? <p className="text-sm text-danger">{bad}</p> : null}
      {url && !bad ? (
        <img src={url} alt={`${label} (preview)`} className="max-h-48 rounded border" />
      ) : null}
    </div>
  );
}

function Verdict({ r }: { r: FaceLabResult }) {
  return (
    <div className="space-y-3">
      <Banner tone={r.above_suggested_threshold ? "info" : "warn"} title={r.above_suggested_threshold ? "Above the engine's suggested threshold" : "Below the engine's suggested threshold"}>
        This is a measurement, not a verification. {r.note}
      </Banner>
      <KeyValues
        items={[
          ["Similarity (cosine)", r.cosine.toFixed(3)],
          ["Score used by the platform (0 to 1)", r.score.toFixed(3)],
          ["Engine's suggested cosine threshold", String(r.recommended_cosine_threshold)],
          ["Live photograph: faces / detection / face share of picture", `${r.probe.faces} / ${r.probe.detection ?? "-"} / ${r.probe.face_ratio ?? "-"}`],
          ["Document photograph: faces / detection / face share of picture", `${r.reference.faces} / ${r.reference.detection ?? "-"} / ${r.reference.face_ratio ?? "-"}`],
          ["Liveness test", "Not available: a photograph of a photograph would pass this match"],
          ["Engine", r.engine],
        ]}
      />
    </div>
  );
}

export function FaceLabPage() {
  const q = useCellQuery(REG.faceLab, (api) => api.regulatory.faceLab());
  const [live, setLive] = useState<File | null>(null);
  const [doc, setDoc] = useState<File | null>(null);
  const run = useCellMutation(async (api, v: { live: File; doc: File }) =>
    api.regulatory.faceLabCompare({ probe_base64: await toBase64(v.live), reference_base64: await toBase64(v.doc) }),
  );
  const ready = live !== null && doc !== null && problem(live) === null && problem(doc) === null;
  return (
    <>
      <PageHeader
        title="Face lab"
        description="Measure how alike two photographs are with the open-source engine, to evaluate it with real photographs. Nothing is stored and nobody is approved: a face match alone is not proof of who someone is, and the engine is not evaluated."
      />
      <QueryView query={q}>
        {(s) => (
          <div className="space-y-5">
            <Card title="Engine and approval gate">
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  <Badge tone={s.configured ? "ok" : "danger"}>{s.configured ? "Face service reachable" : "Face service not set up"}</Badge>
                  <Badge tone="warn">No liveness test</Badge>
                  <Badge tone={s.gate.approvable ? "ok" : "neutral"}>Approval gate: {s.gate.mode.toLowerCase().replaceAll("_", " ")}</Badge>
                </div>
                {s.gate.problems.length ? (
                  <details>
                    <summary className="cursor-pointer text-sm">{s.gate.problems.length} records still missing before any live approval</summary>
                    <ul className="mt-2 list-disc pl-5 text-sm text-ink-muted">
                      {s.gate.problems.map((p) => (
                        <li key={p} className="font-mono text-2xs">
                          {p}
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                <p className="text-sm text-ink-muted">{s.note}</p>
              </div>
            </Card>
            <Card title="Compare two photographs">
              <div className="space-y-4">
                <div className="grid gap-6 sm:grid-cols-2">
                  <Picker label="Live photograph" hint="A clear, front-facing photograph of the person, one face only." file={live} onFile={setLive} />
                  <Picker label="Document photograph" hint="The document page with the portrait, flat and sharp." file={doc} onFile={setDoc} />
                </div>
                <Button pending={run.isPending} isDisabled={!ready} onPress={() => live && doc && run.mutate({ live, doc })}>
                  Compare
                </Button>
                {run.error ? <RegError error={run.error} /> : null}
                {run.data ? <Verdict r={run.data} /> : null}
              </div>
            </Card>
          </div>
        )}
      </QueryView>
    </>
  );
}
