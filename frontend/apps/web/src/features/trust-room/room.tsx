"use client";

import type { TrustRoomMine } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { accessText, canDownload, REQUEST_STATE, requestReady } from "./logic";

const KEY = ["trust-room"];

function Ask() {
  const cat = useCellQuery([...KEY, "catalogue"], (api) => api.trustRoom.catalogue());
  const [f, setF] = useState({ evidence_id: "", organization: "", purpose: "", accept_terms: false });
  const ask = useCellMutation((api, v: typeof f) => api.trustRoom.ask(v), { invalidate: [KEY as never], success: "Request sent" });
  return (
    <QueryView query={cat}>
      {(c) => (
        <Card title="Ask for a restricted document" description="Some assurance documents are shared only under a confidentiality agreement. Tell us who you are and why you need it; our security team decides. Access is for you personally, for a short time, and each download is recorded.">
          {c.items.length === 0 ? (
            <p className="text-sm text-ink-muted">No restricted documents are available through this route right now.</p>
          ) : (
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); ask.mutate(f); }}>
              <Select label="Document" isRequired options={c.items.map((i) => ({ id: i.evidence_id, label: `${i.scheme}: ${i.kind} (${i.legal_entity}, ${i.products.join(", ")}, to ${i.period_end})` }))} value={f.evidence_id} onChange={(v) => setF({ ...f, evidence_id: v })} />
              <TextField label="Your company" isRequired value={f.organization} onChange={(v) => setF({ ...f, organization: v })} />
              <TextField label="Why you need it" isRequired multiline rows={3} value={f.purpose} onChange={(v) => setF({ ...f, purpose: v })} description="For example: vendor security review before a contract. At least a sentence." />
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" checked={f.accept_terms} onChange={(e) => setF({ ...f, accept_terms: e.target.checked })} />
                <span>I accept the confidentiality terms (version {c.terms_version}) and will not share the document or any code for it.</span>
              </label>
              {ask.error ? <ApiErrorView error={ask.error} /> : null}
              <Button type="submit" pending={ask.isPending} isDisabled={!requestReady(f)}>Send request</Button>
            </form>
          )}
        </Card>
      )}
    </QueryView>
  );
}

/** The person fetches the file with the one-time code staff gave them. The code works only for the person who asked and only while the access stands. */
function Fetch() {
  const [token, setToken] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  async function download() {
    setMsg(null);
    const res = await fetch("/api/bff/trust/v1/room/downloads", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ token: token.trim() }) });
    if (!res.ok) {
      setMsg(res.status === 410 ? "That access has expired." : "That code does not work for you right now. It may have expired, been used up or been withdrawn.");
      return;
    }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "assurance-document";
    a.click();
    URL.revokeObjectURL(url);
    setToken("");
    setMsg("Downloaded. Keep it confidential.");
  }
  return (
    <Card title="Download with your access code" description="The code is given to you personally by our security team after approval. It will not work for anyone else.">
      <div className="flex flex-wrap items-end gap-2">
        <TextField label="Access code" mono value={token} onChange={setToken} autoComplete="off" />
        <Button isDisabled={token.trim().length < 20} onPress={() => void download()}>Download</Button>
      </div>
      {msg ? <p role="status" className="mt-2 text-sm text-ink-muted">{msg}</p> : null}
    </Card>
  );
}

export function TrustRoomPage() {
  const mine = useCellQuery([...KEY, "mine"], (api) => api.trustRoom.mine());
  const now = new Date();
  const cols: Column<TrustRoomMine>[] = [
    { key: "o", header: "Company", render: (r) => r.organization },
    { key: "p", header: "Purpose", render: (r) => r.purpose },
    { key: "s", header: "State", render: (r) => <Badge tone={(REQUEST_STATE[r.status] ?? { tone: "neutral" as const }).tone}>{(REQUEST_STATE[r.status] ?? { label: r.status }).label}</Badge> },
    { key: "a", header: "Access", render: (r) => accessText(r, now) },
    { key: "d", header: "", render: (r) => (canDownload(r, now) ? <Badge tone="ok">Use your code below</Badge> : null) },
  ];
  return (
    <div className="space-y-6">
      <PageHeader title="Security documents" description="Request restricted assurance documents, such as audit reports, under a confidentiality agreement." breadcrumbs={[{ label: "Verify API" }, { label: "Security documents" }]} />
      <Banner tone="info" title="Public information needs no request">Our approved public statements and assessments are on the Trust Center page, with no sign-in.</Banner>
      <Ask />
      <QueryView query={mine}>{(d) => <DataTable caption="Your requests" columns={cols} rows={d.items} rowKey={(r) => r.request_id} empty="You have not asked for anything yet." />}</QueryView>
      <Fetch />
    </div>
  );
}
