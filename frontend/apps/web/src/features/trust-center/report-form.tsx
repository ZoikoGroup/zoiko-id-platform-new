"use client";

import { useState, type FormEvent } from "react";

type Receipt = { reference: string; first_response_target: string; note: string };
type Send = (body: unknown) => Promise<{ ok: boolean; status: number; data?: unknown }>;

const defaultSend: Send = async (body) => {
  const res = await fetch("/trust/report", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return { ok: res.ok, status: res.status, data: await res.json().catch(() => undefined) };
};

/** Report a vulnerability (ZID-CP-WF12 WF-12.07). The reply is a reference and a target, never the report. A contact is optional and only the security team sees it. */
export function ReportForm({ send = defaultSend }: { send?: Send }) {
  const [summary, setSummary] = useState("");
  const [affected, setAffected] = useState("");
  const [severity, setSeverity] = useState("medium");
  const [contact, setContact] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setProblem(null);
    const r = await send({ summary, affected, severity, contact: contact.trim() || null }).catch(() => ({ ok: false, status: 0, data: undefined }));
    setBusy(false);
    if (r.ok) setReceipt(r.data as Receipt);
    else if (r.status === 429) setProblem("Too many reports were sent from this network just now. Please try again later, or write to the security address above.");
    else if (r.status === 422) setProblem("Please describe what you found in a sentence or two.");
    else setProblem("The report could not be sent. Please try again, or write to the security address above.");
  }

  if (receipt) {
    return (
      <div role="status" className="space-y-2 rounded-lg border border-line p-4">
        <p className="font-medium text-ink">Thank you. We received your report.</p>
        <p className="text-ink">Your reference is <strong>{receipt.reference}</strong>. Keep it: we will use it when we reply.</p>
        <p className="text-sm text-ink-muted">We aim to respond by {new Date(receipt.first_response_target).toUTCString()}. {receipt.note}</p>
      </div>
    );
  }
  return (
    <form onSubmit={submit} className="space-y-4" aria-describedby="report-help">
      <p id="report-help" className="text-sm text-ink-muted">Only the security team reads this. Please do not include other people's personal data, and do not test against data that is not yours.</p>
      <div>
        <label htmlFor="r-summary" className="block text-sm font-medium text-ink">What did you find?</label>
        <textarea id="r-summary" required minLength={20} maxLength={8000} rows={6} value={summary} onChange={(e) => setSummary(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-surface p-2 text-ink" />
      </div>
      <div>
        <label htmlFor="r-affected" className="block text-sm font-medium text-ink">Where? (address, product or feature)</label>
        <input id="r-affected" type="text" maxLength={2000} value={affected} onChange={(e) => setAffected(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-surface p-2 text-ink" />
      </div>
      <div>
        <label htmlFor="r-severity" className="block text-sm font-medium text-ink">How serious do you think it is?</label>
        <select id="r-severity" value={severity} onChange={(e) => setSeverity(e.target.value)} className="mt-1 rounded-md border border-line bg-surface p-2 text-ink">
          {["critical", "high", "medium", "low", "info"].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      <div>
        <label htmlFor="r-contact" className="block text-sm font-medium text-ink">How can we reach you? (optional)</label>
        <input id="r-contact" type="text" maxLength={200} autoComplete="email" value={contact} onChange={(e) => setContact(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-surface p-2 text-ink" />
        <p className="mt-1 text-xs text-ink-muted">Leave it empty to report anonymously; we then cannot reply.</p>
      </div>
      {problem ? <p role="alert" className="text-sm text-danger-700">{problem}</p> : null}
      <button type="submit" disabled={busy} className="rounded-md bg-brand-600 px-4 py-2 font-medium text-white disabled:opacity-60">{busy ? "Sending…" : "Send report"}</button>
    </form>
  );
}
