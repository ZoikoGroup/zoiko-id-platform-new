"use client";

import { CHECK_METHODS, type CheckCase, type CheckDocument, type CheckMethod } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, EmptyState, PageHeader, Select, TextField, type Column, type Tone } from "@zoiko/ui";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { PolicyCard } from "./policy-card";

/** ZID-DTV-WF-05 verification checks (A01-A10; CR-112 to CR-116): open a case for a person, check their email or phone, address and proof of
 * address, and read each result on its own. Nothing here says "verified": each assertion shows what it proves and what it does not. */

const KEY = ["checks"] as const;
const caseKey = (id: string) => ["checks", id] as const;
const LABEL: Record<CheckMethod, string> = {
  EMAIL_CHALLENGE: "Email code or link",
  PHONE_SMS_CHALLENGE: "Phone code by SMS",
  PHONE_VOICE_CHALLENGE: "Phone code by voice call",
  CARRIER_INTELLIGENCE: "Carrier check",
  ADDRESS_LOOKUP: "Address format and location",
  ADDRESS_DOCUMENT: "Proof of address document",
  ADDRESS_DATA_SOURCE: "Licensed address source",
  ADDRESS_POSTAL_CHALLENGE: "Code by post",
  DEVICE_RISK: "Device risk",
};
const OUTCOME_TONE: Record<string, Tone> = { PASS: "ok", REVIEW: "warn", INDETERMINATE: "neutral", UNAVAILABLE: "neutral", FAIL: "danger", EXPIRED: "neutral" };
const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now()));

function OpenCase({ onOpened }: { onOpened: (c: CheckCase) => void }) {
  const [subject, setSubject] = useState("");
  const [market, setMarket] = useState("GB");
  const [purpose, setPurpose] = useState("ONBOARDING");
  const [methods, setMethods] = useState<CheckMethod[]>(["EMAIL_CHALLENGE", "ADDRESS_LOOKUP"]);
  const open = useCellMutation(
    (api) => api.checks.open({ subject_ref: subject.trim(), market: market.trim().toUpperCase(), purpose: purpose.trim(), methods }, newKey()),
    { invalidate: [[...KEY]], success: (c) => (c.status === "CREATED" ? "Case opened: this step is unavailable" : "Case opened"), onSuccess: (c) => onOpened(c) },
  );
  const toggle = (m: CheckMethod) => setMethods((p) => (p.includes(m) ? p.filter((x) => x !== m) : [...p, m]));
  return (
    <Card title="Open a case" description="For one person, purpose and country. Use your own reference for the person: never an email address, phone number or name.">
      <div className="grid gap-3 sm:grid-cols-3">
        <TextField label="Your reference for the person" value={subject} onChange={setSubject} isRequired description="e.g. cust-0042" />
        <TextField label="Country (ISO)" value={market} onChange={setMarket} isRequired />
        <TextField label="Purpose" value={purpose} onChange={setPurpose} isRequired />
      </div>
      <p className="mt-4 text-sm font-medium text-ink">Checks to ask for</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {CHECK_METHODS.map((m) => (
          <Button key={m} size="xs" variant={methods.includes(m) ? "primary" : "secondary"} onPress={() => toggle(m)} aria-pressed={methods.includes(m)}>
            {LABEL[m]}
          </Button>
        ))}
      </div>
      {open.error ? <ApiErrorView error={open.error} /> : null}
      <div className="mt-4 flex justify-end">
        <Button onPress={() => open.mutate(undefined)} pending={open.isPending} isDisabled={!subject.trim() || !methods.length || market.trim().length !== 2}>
          Open case
        </Button>
      </div>
    </Card>
  );
}

function Contact({ c }: { c: CheckCase }) {
  const [type, setType] = useState<"EMAIL" | "PHONE">("EMAIL");
  const [value, setValue] = useState("");
  const [contactId, setContactId] = useState<string | null>(null);
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const session = `console-${c.case_id.slice(0, 8)}`;
  const add = useCellMutation((api) => api.checks.addContact(c.case_id, { type, value: value.trim() }), {
    success: (r) => `Saved ${r.masked}. A format that is accepted does not mean it belongs to the person.`,
    onSuccess: (r) => {
      setContactId(r.contact_id);
      setChallengeId(null);
    },
  });
  const send = useCellMutation(
    (api, channel: "EMAIL" | "SMS") => api.checks.challenge(contactId ?? "", { channel, session_ref: session }, newKey()),
    { success: (r) => `Sent to ${r.masked_target ?? "the contact"}; it expires in 5 minutes`, onSuccess: (r) => setChallengeId(r.challenge_id) },
  );
  const confirm = useCellMutation((api) => api.checks.confirm(contactId ?? "", { challenge_id: challengeId ?? "", code: code.trim(), session_ref: session }), {
    invalidate: [caseKey(c.case_id)],
    success: "Confirmed: control of this inbox or number now, not who the person is",
  });
  return (
    <Card title="Email or phone (A02-A04)" description="A one-time code proves only that the person controls the inbox or number right now.">
      <div className="grid gap-3 sm:grid-cols-[10rem_1fr_auto] sm:items-end">
        <Select label="Type" value={type} onChange={(v) => setType(v === "PHONE" ? "PHONE" : "EMAIL")} options={[{ id: "EMAIL", label: "Email" }, { id: "PHONE", label: "Phone" }]} />
        <TextField label={type === "EMAIL" ? "Email address" : "Phone number"} value={value} onChange={setValue} isRequired />
        <Button onPress={() => add.mutate(undefined)} pending={add.isPending} isDisabled={!value.trim()}>
          Save
        </Button>
      </div>
      {contactId ? (
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <Button variant="secondary" onPress={() => send.mutate(type === "EMAIL" ? "EMAIL" : "SMS")} pending={send.isPending}>
            Send code
          </Button>
          {challengeId ? (
            <>
              <TextField label="Code from the message" value={code} onChange={setCode} />
              <Button onPress={() => confirm.mutate(undefined)} pending={confirm.isPending} isDisabled={!code.trim()}>
                Confirm
              </Button>
            </>
          ) : null}
        </div>
      ) : null}
      {[add.error, send.error, confirm.error].map((e, i) => (e ? <ApiErrorView key={i} error={e} /> : null))}
    </Card>
  );
}

function Address({ c }: { c: CheckCase }) {
  const [f, setF] = useState({ line1: "", city: "", postal_code: "", country: c.market });
  const [doc, setDoc] = useState({ upload_ref: "", doc_type: "UTILITY_BILL", issuer: "", issued_on: "", name_on_document: "", subject_name: "" });
  const [postalId, setPostalId] = useState<string | null>(null);
  const [postalCode, setPostalCode] = useState("");
  const docs = useCellQuery(["checks", c.case_id, "documents"], (api) => api.checks.documents(c.case_id));
  const inv = { invalidate: [caseKey(c.case_id), ["checks", c.case_id, "documents"]] };
  const save = useCellMutation((api) => api.checks.setAddress(c.case_id, f), { ...inv, success: (r) => `Address saved (format: ${r.format_outcome ?? "not checked"})` });
  const add = useCellMutation((api) => api.checks.addDocument(c.case_id, { ...doc, address_on_document: f }), { ...inv, success: (r) => `Document assessed: ${r.assessment}` });
  const review = useCellMutation(
    (api, v: { d: CheckDocument; finding: "CONFIRMED" | "NOT_SUPPORTED" }) =>
      api.checks.review(c.case_id, v.d.document_id, { finding: v.finding, reason_codes: v.finding === "NOT_SUPPORTED" ? ["ADDRESS_MISMATCH"] : [] }),
    { ...inv, success: "Review recorded" },
  );
  const check = useCellMutation((api, d: CheckDocument) => api.checks.check(c.case_id, d.document_id, { agrees: true, reason_codes: [] }), {
    ...inv,
    success: "Checked by a second person: association recorded",
  });
  const post = useCellMutation((api) => api.checks.postal(c.case_id), { success: "A code is on its way by post", onSuccess: (r) => setPostalId(r.challenge_id) });
  const confirmPost = useCellMutation((api) => api.checks.confirmPostal(c.case_id, postalId ?? "", postalCode.trim()), { ...inv, success: "Postal code accepted" });
  const cols: Column<CheckDocument>[] = [
    { key: "type", header: "Type", render: (d) => d.doc_type },
    { key: "assessment", header: "Rules", render: (d) => <Badge tone={d.assessment === "SUPPORTS" ? "ok" : d.assessment === "REVIEW" ? "warn" : "danger"}>{d.assessment}</Badge> },
    { key: "findings", header: "Findings", render: (d) => d.findings.join(", ") || "-" },
    { key: "status", header: "Review", render: (d) => d.status },
    {
      key: "act",
      header: "Decide",
      align: "end",
      render: (d) =>
        d.status === "AWAITING_REVIEW" ? (
          <span className="inline-flex gap-1.5">
            <Button size="xs" onPress={() => review.mutate({ d, finding: "CONFIRMED" })}>Confirm</Button>
            <Button size="xs" variant="secondary" onPress={() => review.mutate({ d, finding: "NOT_SUPPORTED" })}>Not supported</Button>
          </span>
        ) : d.status === "AWAITING_CHECKER" ? (
          <Button size="xs" onPress={() => check.mutate(d)}>Check (second person)</Button>
        ) : (
          "Done"
        ),
    },
  ];
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const setD = (k: keyof typeof doc) => (v: string) => setDoc((p) => ({ ...p, [k]: v }));
  return (
    <Card title="Address and proof of address (A05-A08, A10)" description="An address that is entered or found is not proof that anyone lives there. A document is reviewed by one person; only a second person confirms the link to the person.">
      <div className="grid gap-3 sm:grid-cols-4">
        <TextField label="Address line" value={f.line1} onChange={set("line1")} isRequired />
        <TextField label="City" value={f.city} onChange={set("city")} isRequired />
        <TextField label="Postal code" value={f.postal_code} onChange={set("postal_code")} description="leave empty where the country has none" />
        <TextField label="Country (ISO)" value={f.country} onChange={set("country")} isRequired />
      </div>
      <div className="mt-3 flex justify-end">
        <Button onPress={() => save.mutate(undefined)} pending={save.isPending} isDisabled={!f.line1.trim() || !f.city.trim()}>
          Save address
        </Button>
      </div>
      <p className="mt-5 text-sm font-medium text-ink">Proof of address document</p>
      <div className="mt-2 grid gap-3 sm:grid-cols-3">
        <TextField label="Upload reference" value={doc.upload_ref} onChange={setD("upload_ref")} description="the evidence upload holding the file" />
        <TextField label="Document type" value={doc.doc_type} onChange={setD("doc_type")} />
        <TextField label="Issuer" value={doc.issuer} onChange={setD("issuer")} />
        <TextField label="Issued on (YYYY-MM-DD)" value={doc.issued_on} onChange={setD("issued_on")} />
        <TextField label="Name on the document" value={doc.name_on_document} onChange={setD("name_on_document")} />
        <TextField label="Person's name (compared, never stored)" value={doc.subject_name} onChange={setD("subject_name")} />
      </div>
      <div className="mt-3 flex justify-end">
        <Button variant="secondary" onPress={() => add.mutate(undefined)} pending={add.isPending} isDisabled={!doc.upload_ref.trim() || !doc.issued_on.trim() || !doc.subject_name.trim()}>
          Add document
        </Button>
      </div>
      <QueryView query={docs}>
        {(d) => <DataTable caption="Documents" columns={cols} rows={d.items} rowKey={(x) => x.document_id} empty={<EmptyState icon={ShieldCheck} title="No documents yet" />} />}
      </QueryView>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <Button variant="secondary" onPress={() => post.mutate(undefined)} pending={post.isPending}>
          Send a code by post
        </Button>
        {postalId ? (
          <>
            <TextField label="Code from the letter" value={postalCode} onChange={setPostalCode} />
            <Button onPress={() => confirmPost.mutate(undefined)} pending={confirmPost.isPending} isDisabled={!postalCode.trim()}>
              Confirm
            </Button>
          </>
        ) : null}
      </div>
      {[save.error, add.error, review.error, check.error, post.error, confirmPost.error].map((e, i) => (e ? <ApiErrorView key={i} error={e} /> : null))}
    </Card>
  );
}

function CaseDetail({ c }: { c: CheckCase }) {
  const ev = useCellQuery(caseKey(c.case_id), (api) => api.checks.evidence(c.case_id));
  const close = useCellMutation((api) => api.checks.close(c.case_id), { invalidate: [[...KEY], caseKey(c.case_id)], success: "Case closed" });
  const has = (m: CheckMethod) => c.available_methods.includes(m);
  return (
    <div className="space-y-5">
      {c.message ? (
        <Banner tone="warn" title={c.message}>
          {c.unavailable_reasons.join(" · ")}
        </Banner>
      ) : null}
      <Card
        title="What is verified and what is not (A09)"
        description="Each result stands alone. There is no overall verified result."
        actions={
          c.status !== "CLOSED" ? (
            <Button size="sm" variant="danger" onPress={() => close.mutate(undefined)} pending={close.isPending}>
              Close case
            </Button>
          ) : undefined
        }
      >
        <QueryView query={ev}>
          {(d) =>
            d.assertions.length ? (
              <ul className="space-y-2">
                {d.assertions.map((a) => (
                  <li key={a.type} className="rounded-card border border-border p-3 text-sm">
                    <span className="flex flex-wrap items-center gap-2">
                      <strong>{a.type}</strong>
                      <Badge tone={OUTCOME_TONE[a.outcome] ?? "neutral"}>{a.outcome}</Badge>
                      <span className="text-ink-muted">{a.source ?? ""}</span>
                      <DateTime value={a.observed_at} />
                    </span>
                    <span className="mt-1 block text-ink-muted">Proves: {a.proves} Does not prove: {a.does_not_prove}</span>
                    {a.reason_codes.length ? <span className="mt-1 block text-ink-muted">Reasons: {a.reason_codes.join(", ")}</span> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState icon={ShieldCheck} title="No results yet" />
            )
          }
        </QueryView>
      </Card>
      {c.status !== "CLOSED" && (has("EMAIL_CHALLENGE") || has("PHONE_SMS_CHALLENGE")) ? <Contact c={c} /> : null}
      {c.status !== "CLOSED" && ["ADDRESS_LOOKUP", "ADDRESS_DOCUMENT", "ADDRESS_POSTAL_CHALLENGE"].some((m) => has(m as CheckMethod)) ? <Address c={c} /> : null}
    </div>
  );
}

export function ChecksPage() {
  const q = useCellQuery([...KEY], (api) => api.checks.cases());
  const [selected, setSelected] = useState<CheckCase | null>(null);
  const cols: Column<CheckCase>[] = [
    { key: "ref", header: "Person", pinned: true, render: (c) => c.subject_ref },
    { key: "status", header: "Status", render: (c) => <Badge tone={c.status === "CREATED" ? "warn" : "neutral"}>{c.status}</Badge> },
    { key: "market", header: "Country · purpose", render: (c) => `${c.market} · ${c.purpose}` },
    { key: "methods", header: "Cleared checks", render: (c) => c.available_methods.map((m) => LABEL[m]).join(", ") || "none" },
    { key: "at", header: "Opened", render: (c) => <DateTime value={c.created_at} /> },
    { key: "open", header: "", align: "end", render: (c) => <Button size="xs" variant="secondary" onPress={() => setSelected(c)}>Open</Button> },
  ];
  return (
    <>
      <PageHeader
        title="Verification checks"
        description="Email, phone, address and device checks for a person. A check runs only where it is approved for the country and purpose; nothing is switched on by default."
      />
      <div className="space-y-5">
        <PolicyCard />
        <OpenCase onOpened={setSelected} />
        <Card padded={false} title="Cases">
          <QueryView query={q}>
            {(d) => <DataTable caption="Verification cases" columns={cols} rows={d.items} rowKey={(c) => c.case_id} empty={<EmptyState icon={ShieldCheck} title="No cases yet" />} />}
          </QueryView>
        </Card>
        {selected ? <CaseDetail key={selected.case_id} c={selected} /> : null}
      </div>
    </>
  );
}
