"use client";

import type { IssuerKind, IssuerStatement, IssuerStatementIn } from "@zoiko/api-client";
import { Badge, Button, Card, DataTable, DateTime, Dialog, EmptyState, FieldGroup, JsonView, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { Landmark, X } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { COUNTRY_OPTIONS, Hash, isoFromLocal, list, Mono, opt, REG, Reasons, RegError, Verdict } from "./common";

type Authority = IssuerStatementIn["authority"];
type StatementKind = NonNullable<IssuerStatementIn["statement"]>;

const KIND_OPTIONS: { id: IssuerKind; label: string }[] = [
  { id: "UNIVERSITY", label: "UNIVERSITY (awards degrees and diplomas)" },
  { id: "REGULATOR", label: "REGULATOR (licenses or regulates a profession)" },
  { id: "EMPLOYER", label: "EMPLOYER (states employment)" },
  { id: "INSURER", label: "INSURER (states cover)" },
  { id: "VENDOR", label: "VENDOR (training or certification vendor)" },
  { id: "ACCREDITOR", label: "ACCREDITOR (accredits other issuers)" },
];
const AUTHORITY_OPTIONS: { id: Authority; label: string }[] = [
  { id: "OFFICIAL", label: "OFFICIAL (a recorded legal or regulatory basis to issue)" },
  { id: "UNRECORDED", label: "UNRECORDED (no recorded basis; trusted for no claim type)" },
];
const STATEMENT_OPTIONS: { id: StatementKind; label: string }[] = [
  { id: "UPDATE", label: "UPDATE (adds facts, or ends them from today)" },
  { id: "CORRECTION", label: "CORRECTION (changes what an earlier statement said about the past)" },
];

// the backend's own rules (IssuerStatementIn and issuer_history), checked here so the form says what is wrong before sending
const ISSUER_ID = /^[a-z0-9][a-z0-9-]{0,127}$/;
const CLAIM_TYPE = /^[A-Z][A-Z0-9_]{0,63}$/;
const HTTPS = /^https:\/\/[^\s/]+\S{0,480}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const LOCATOR = /^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$/;
const MIN_REASON = 10;

type NameRow = { name: string; from: string; until: string };
type AccreditationRow = { body: string; program: string; from: string; until: string };
type IssuerForm = {
  statement: StatementKind;
  reason: string;
  issuer_id: string;
  kind: IssuerKind;
  authority: Authority;
  countries: string[];
  claim_types: string;
  names: NameRow[];
  accreditations: AccreditationRow[];
  revoked_on: string;
  successor_of: string;
  source_url: string;
  source_hash: string;
  evidence_locator: string;
};

const str = (v: unknown) => (typeof v === "string" ? v : "");
const day = (v: unknown) => str(v).slice(0, 10);
const rows = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => typeof x === "object" && x !== null) : []);
const strings = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const BLANK_NAME: NameRow = { name: "", from: "", until: "" };
const BLANK_ACCREDITATION: AccreditationRow = { body: "", program: "", from: "", until: "" };

/** A statement is a full record, so a new one starts from the latest (an update that leaves a fact out would remove it); the source is always new. */
function startFrom(issuerId: string | null, latest: IssuerStatement | undefined): IssuerForm {
  const r = latest?.record ?? {};
  const names = rows(r.names).map((n) => ({ name: str(n.name), from: day(n.from), until: day(n.until) }));
  return {
    statement: "UPDATE",
    reason: "",
    issuer_id: issuerId ?? "",
    kind: KIND_OPTIONS.find((o) => o.id === r.kind)?.id ?? "UNIVERSITY",
    authority: r.authority === "OFFICIAL" ? "OFFICIAL" : latest ? "UNRECORDED" : "OFFICIAL",
    countries: strings(r.countries),
    claim_types: strings(r.claim_types).join(", "),
    names: names.length ? names : [{ ...BLANK_NAME }],
    accreditations: rows(r.accreditations).map((a) => ({ body: str(a.body), program: str(a.program), from: day(a.from), until: day(a.until) })),
    revoked_on: day(r.revoked_on),
    successor_of: str(r.successor_of),
    source_url: "",
    source_hash: "",
    evidence_locator: "",
  };
}

const claimTypes = (v: string) => list(v).map((c) => c.toUpperCase());

function toBody(f: IssuerForm): IssuerStatementIn {
  return {
    issuer_id: f.issuer_id.trim(),
    issuer_kind: f.kind,
    authority: f.authority,
    countries: f.countries,
    claim_types: claimTypes(f.claim_types),
    names: f.names.map((n) => ({ name: n.name.trim(), from: opt(n.from) ?? null, until: opt(n.until) ?? null })),
    accreditations: f.accreditations.map((a) => ({ body: a.body.trim(), program: opt(a.program) ?? null, from: a.from, until: opt(a.until) ?? null })),
    revoked_on: opt(f.revoked_on) ?? null,
    successor_of: opt(f.successor_of) ?? null,
    statement: f.statement,
    reason: f.statement === "CORRECTION" ? f.reason.trim() : null,
    source_url: f.source_url.trim(),
    source_hash: f.source_hash.trim().toLowerCase(),
    evidence_locator: f.evidence_locator.trim(),
  };
}

/** Field problems for values already typed; an empty required field only keeps Record disabled. */
function problems(f: IssuerForm) {
  const claims = claimTypes(f.claim_types);
  const hash = f.source_hash.trim().toLowerCase();
  return {
    issuer_id: f.issuer_id.trim() && !ISSUER_ID.test(f.issuer_id.trim()) ? "Lowercase letters, digits and hyphens only" : undefined,
    claim_types: claims.some((c) => !CLAIM_TYPE.test(c)) ? "Codes are capital letters, digits and underscores, starting with a letter" : undefined,
    authority: claims.length && f.authority !== "OFFICIAL" ? "An issuer trusted for any claim type needs OFFICIAL authority" : undefined,
    reason: f.statement === "CORRECTION" && f.reason.trim().length < MIN_REASON ? `Say why, in at least ${MIN_REASON} characters` : undefined,
    source_url: f.source_url.trim() && !HTTPS.test(f.source_url.trim()) ? "An https address" : undefined,
    source_hash: hash && !SHA256.test(hash) ? "64 hex characters" : undefined,
    evidence_locator: f.evidence_locator.trim() && !LOCATOR.test(f.evidence_locator.trim()) ? "At least 3 characters: letters, digits and . _ : / # @ + = -" : undefined,
  };
}

function RecordIssuerDialog({ issuer, latest, onClose, onRecorded }: { issuer: string | null; latest: IssuerStatement | undefined; onClose: () => void; onRecorded: (issuerId: string) => void }) {
  const [f, setF] = useState(() => startFrom(issuer, latest));
  const set = (k: "reason" | "issuer_id" | "claim_types" | "revoked_on" | "successor_of" | "source_url" | "source_hash" | "evidence_locator") => (v: string) =>
    setF((p) => ({ ...p, [k]: v }));
  const setName = (i: number, k: keyof NameRow) => (v: string) => setF((p) => ({ ...p, names: p.names.map((n, j) => (j === i ? { ...n, [k]: v } : n)) }));
  const setAccreditation = (i: number, k: keyof AccreditationRow) => (v: string) =>
    setF((p) => ({ ...p, accreditations: p.accreditations.map((a, j) => (j === i ? { ...a, [k]: v } : a)) }));
  const save = useCellMutation((api, body: IssuerStatementIn) => api.regulatory.recordIssuerStatement(body), {
    invalidate: [REG.issuers, ["regulatory", "award-check"]],
    success: (r) =>
      r.retroactive_changes.length
        ? `Correction recorded as statement ${r.seq} of ${r.issuer_id}; it changes ${r.retroactive_changes.length} earlier ${r.retroactive_changes.length === 1 ? "fact" : "facts"}`
        : `Statement ${r.seq} of ${r.issuer_id} recorded`,
    onSuccess: (r) => onRecorded(r.issuer_id),
  });
  const p = problems(f);
  const ready =
    Object.values(p).every((v) => v === undefined) &&
    f.issuer_id.trim() &&
    f.names.length > 0 &&
    f.names.every((n) => n.name.trim()) &&
    f.accreditations.every((a) => a.body.trim() && a.from) &&
    f.source_url.trim() &&
    f.source_hash.trim() &&
    f.evidence_locator.trim();
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title="Record issuer statement"
      description="What an official record says about this issuer today (WF-04), with the source it came from so anyone can check it. Statements are append-only: each one is the issuer's full record, and earlier statements stay visible."
      size="xl"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button onPress={() => save.mutate(toBody(f))} pending={save.isPending} isDisabled={!ready}>
            Record statement
          </Button>
        </>
      }
    >
      <div className="space-y-6">
        <FieldGroup
          legend="The statement"
          description={latest ? `Starts from statement ${latest.seq} of ${latest.issuer_id}. Keep what is still true; end facts from today rather than deleting them.` : "The first statement about an issuer is always an UPDATE."}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Select
              label="Statement"
              options={STATEMENT_OPTIONS}
              value={f.statement}
              onChange={(v) => setF((x) => ({ ...x, statement: v === "CORRECTION" ? "CORRECTION" : "UPDATE" }))}
              description="A correction is needed to remove or shorten an accreditation or name, or move or backdate a revocation"
              isRequired
            />
            {f.statement === "CORRECTION" ? (
              <TextField
                label="Reason for the correction"
                description={`what the earlier statement got wrong; at least ${MIN_REASON} characters`}
                placeholder="The register entry recorded on 2026-03-02 gave the wrong accreditation start date"
                value={f.reason}
                onChange={set("reason")}
                errorMessage={f.reason ? p.reason : undefined}
                multiline
                rows={2}
                isRequired
              />
            ) : null}
          </div>
        </FieldGroup>
        <FieldGroup legend="The issuer">
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField label="Issuer ID" description="lowercase, as in the issuer registry" placeholder="uni-of-example" value={f.issuer_id} onChange={set("issuer_id")} errorMessage={p.issuer_id} mono isRequired />
            <Select label="Kind" options={KIND_OPTIONS} value={f.kind} onChange={(v) => setF((x) => ({ ...x, kind: KIND_OPTIONS.find((o) => o.id === v)?.id ?? x.kind }))} isRequired />
            <Select
              label="Authority"
              description="whether a legal or regulatory basis to issue is on record"
              options={AUTHORITY_OPTIONS}
              value={f.authority}
              onChange={(v) => setF((x) => ({ ...x, authority: v === "UNRECORDED" ? "UNRECORDED" : "OFFICIAL" }))}
              errorMessage={p.authority}
              isRequired
            />
            <TextField
              label="Claim types"
              description="codes it is trusted to issue, comma separated; optional"
              placeholder="DEGREE, PROFESSIONAL_LICENCE"
              value={f.claim_types}
              onChange={set("claim_types")}
              errorMessage={p.claim_types}
              mono
            />
            <div className="space-y-1.5">
              <Select
                label="Countries"
                description="where it issues; pick each one"
                placeholder="Add a country…"
                options={COUNTRY_OPTIONS.filter((o) => !f.countries.includes(o.id))}
                value={null}
                onChange={(v) => setF((x) => ({ ...x, countries: x.countries.includes(v) ? x.countries : [...x.countries, v] }))}
              />
              {f.countries.length ? (
                <ul className="flex flex-wrap gap-1" aria-label="Chosen countries">
                  {f.countries.map((c) => (
                    <li key={c} className="inline-flex items-center gap-0.5">
                      <Badge>{c}</Badge>
                      <Button size="xs" variant="ghost" aria-label={`Remove ${c}`} onPress={() => setF((x) => ({ ...x, countries: x.countries.filter((y) => y !== c) }))}>
                        <X aria-hidden className="size-3" />
                      </Button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
            <TextField label="Revoked on" description="optional; the day it stopped being an issuer" type="date" value={f.revoked_on} onChange={set("revoked_on")} />
            <TextField label="Successor of" description="optional; the issuer ID it replaced" placeholder="example-college" value={f.successor_of} onChange={set("successor_of")} mono />
          </div>
        </FieldGroup>
        <FieldGroup legend="Names" description="Every name it has issued under, with the dates it used each one. At least one.">
          {f.names.map((n, i) => (
            <div key={i} className="grid items-end gap-3 sm:grid-cols-[2fr_1fr_1fr_auto]">
              <TextField label={`Name ${i + 1}`} placeholder="University of Example" value={n.name} onChange={setName(i, "name")} isRequired />
              <TextField label={`Name ${i + 1} used from`} type="date" value={n.from} onChange={setName(i, "from")} />
              <TextField label={`Name ${i + 1} used until`} type="date" value={n.until} onChange={setName(i, "until")} />
              <Button size="sm" variant="ghost" aria-label={`Remove name ${i + 1}`} isDisabled={f.names.length === 1} onPress={() => setF((x) => ({ ...x, names: x.names.filter((_, j) => j !== i) }))}>
                <X aria-hidden className="size-4" />
              </Button>
            </div>
          ))}
          <Button size="sm" variant="secondary" onPress={() => setF((x) => ({ ...x, names: [...x.names, { ...BLANK_NAME }] }))}>
            Add name
          </Button>
        </FieldGroup>
        <FieldGroup legend="Accreditations" description="Who accredits it, for which programme, and from when. Leave the programme empty when the whole institution is accredited.">
          {f.accreditations.map((a, i) => (
            <div key={i} className="grid items-end gap-3 sm:grid-cols-[1fr_1fr_1fr_1fr_auto]">
              <TextField label={`Accreditation ${i + 1} body`} description="the accrediting issuer's ID" placeholder="example-accreditor" value={a.body} onChange={setAccreditation(i, "body")} mono isRequired />
              <TextField label={`Accreditation ${i + 1} programme`} placeholder="BSc Nursing" value={a.program} onChange={setAccreditation(i, "program")} />
              <TextField label={`Accreditation ${i + 1} from`} type="date" value={a.from} onChange={setAccreditation(i, "from")} isRequired />
              <TextField label={`Accreditation ${i + 1} until`} type="date" value={a.until} onChange={setAccreditation(i, "until")} />
              <Button size="sm" variant="ghost" aria-label={`Remove accreditation ${i + 1}`} onPress={() => setF((x) => ({ ...x, accreditations: x.accreditations.filter((_, j) => j !== i) }))}>
                <X aria-hidden className="size-4" />
              </Button>
            </div>
          ))}
          <Button size="sm" variant="secondary" onPress={() => setF((x) => ({ ...x, accreditations: [...x.accreditations, { ...BLANK_ACCREDITATION }] }))}>
            Add accreditation
          </Button>
        </FieldGroup>
        <FieldGroup legend="Official source" description="Where this came from, so an independent reviewer can fetch it again and compare.">
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField
              label="Source URL"
              description="the official record, https only"
              placeholder="https://register.example.gov/institutions/1234"
              type="url"
              value={f.source_url}
              onChange={set("source_url")}
              errorMessage={p.source_url}
              isRequired
            />
            <TextField label="Source hash" description="SHA-256 of what the source returned" placeholder={"3f2a…(64 hex characters)"} value={f.source_hash} onChange={set("source_hash")} errorMessage={p.source_hash} mono isRequired />
            <TextField
              label="Evidence locator"
              description="where the saved copy of the response is kept"
              placeholder="s3://evidence/issuers/uni-of-example/2026-11-16.html"
              value={f.evidence_locator}
              onChange={set("evidence_locator")}
              errorMessage={p.evidence_locator}
              mono
              isRequired
            />
          </div>
        </FieldGroup>
        {save.error ? <RegError error={save.error} /> : null}
      </div>
    </Dialog>
  );
}

function AwardCheck({ issuer }: { issuer: string }) {
  const [f, setF] = useState({ program: "", awarded_on: "", known_at: "" });
  const [asked, setAsked] = useState<typeof f | null>(null);
  const q = useCellQuery(
    ["regulatory", "award-check", issuer, asked],
    (api) => api.regulatory.awardCheck(issuer, asked!.program, asked!.awarded_on, asked!.known_at ? isoFromLocal(asked!.known_at) : undefined),
    { enabled: asked !== null },
  );
  return (
    <Card title="Was this issuer trusted when the award was made?" description="Judged against what the ledger knew at the time you choose (default now). Unknown history is REVIEW, never PASS.">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setAsked({ ...f });
        }}
      >
        <TextField label="Programme" value={f.program} onChange={(v) => setF((p) => ({ ...p, program: v }))} isRequired />
        <TextField label="Awarded on" type="date" value={f.awarded_on} onChange={(v) => setF((p) => ({ ...p, awarded_on: v }))} isRequired />
        <TextField label="As known at" type="datetime-local" value={f.known_at} onChange={(v) => setF((p) => ({ ...p, known_at: v }))} />
        <Button type="submit" variant="secondary" isDisabled={!f.program || !f.awarded_on}>
          Check
        </Button>
      </form>
      {asked ? (
        <div className="mt-3">
          <QueryView query={q}>
            {(d) => (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Verdict ok={d.outcome === "PASS"} yes="Pass" no="Review" />
                <Badge>{d.accreditation}</Badge>
                {d.name_at_award ? <span>named {d.name_at_award} at award</span> : null}
                <Reasons reasons={d.reasons} none="No concerns" />
              </div>
            )}
          </QueryView>
        </div>
      ) : null}
    </Card>
  );
}

export function IssuersPage() {
  const issuers = useCellQuery(REG.issuers, (api) => api.regulatory.issuers());
  const [issuer, setIssuer] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const history = useCellQuery([...REG.issuers, issuer], (api) => api.regulatory.issuerStatements(issuer!), { enabled: issuer !== null });
  const columns: Column<IssuerStatement>[] = [
    { key: "seq", header: "#", pinned: true, render: (s) => s.seq, sortValue: (s) => s.seq },
    { key: "kind", header: "Statement", render: (s) => <Badge tone={s.statement === "CORRECTION" ? "warn" : "neutral"}>{s.statement}</Badge> },
    { key: "record", header: "What it says", render: (s) => <JsonView value={s.record} label="Record" /> },
    { key: "reason", header: "Correction reason", render: (s) => s.reason ?? "-" },
    { key: "source", header: "Source", render: (s) => <Hash value={s.source_hash} /> },
    { key: "by", header: "Recorded", render: (s) => <span><Mono>{s.recorded_by}</Mono> · <DateTime value={s.recorded_at} /></span> },
  ];
  return (
    <>
      <PageHeader
        title="Issuers"
        description="Credential issuers' history (WF-04): append-only statements and corrections, so a past award is judged by what was true and known then, not by today's list."
        actions={
          // a statement about a chosen issuer starts from its latest one, so wait for that history
          <Button onPress={() => setRecording(true)} isDisabled={issuer !== null && history.isPending}>
            Record issuer statement
          </Button>
        }
      />
      <div className="space-y-5">
        <QueryView query={issuers}>
          {(d) =>
            d.items.length ? (
              <Select label="Issuer" value={issuer} onChange={setIssuer} options={d.items.map((i) => ({ id: i, label: i }))} className="max-w-sm" />
            ) : (
              <EmptyState icon={Landmark} title="No issuer history">
                No statement has been recorded for any issuer. Record the first one from an official source with Record issuer statement.
              </EmptyState>
            )
          }
        </QueryView>
        {issuer ? (
          <>
            <Card padded={false} title={`History of ${issuer}`}>
              <QueryView query={history}>
                {(d) => <DataTable caption="Issuer statements" columns={columns} rows={d.items} rowKey={(s) => s.statement_id} />}
              </QueryView>
            </Card>
            <AwardCheck issuer={issuer} />
          </>
        ) : null}
      </div>
      {recording ? (
        <RecordIssuerDialog
          issuer={issuer}
          latest={issuer ? history.data?.items.at(-1) : undefined}
          onClose={() => setRecording(false)}
          onRecorded={(id) => {
            setRecording(false);
            setIssuer(id);
          }}
        />
      ) : null}
    </>
  );
}
