"use client";

import { ApiError, type IdvCase } from "@zoiko/api-client";
import { Banner, Button, Card, LoadingRows, PageHeader, TextField } from "@zoiko/ui";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { AutoContinue } from "@/components/auto-continue";
import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";
import { safeNext } from "@/lib/continue";

import { DocumentCapture } from "./document-capture";
import { LiveCapture } from "./live-capture";
import {
  AADHAAR_MARKET,
  applicantText,
  detailsProblem,
  DOC_LABEL,
  DOC_OPTIONS,
  docKindOf,
  fileToBase64,
  imageProblem,
  isAuto,
  isFace,
  latestCheck,
  MY_IDV_KEY,
  NO_DETAILS,
  routeFor,
  simpleState,
  trimDetails,
  type Declared,
  type DocKind,
} from "./logic";

function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && [403, 404, 409, 422, 429, 503].includes(error.status)) {
    return <Banner tone="danger" title="This could not be done">{applicantText(String(error.code ?? ""))}</Banner>;
  }
  return <ApiErrorView error={error} />;
}

/** The four details the platform compares with its own reading of a passport. Typed once, sent with the photograph, never kept. */
function DetailsForm({ value, onChange }: { value: Declared; onChange: (d: Declared) => void }) {
  const set = (k: keyof Declared) => (v: string) => onChange({ ...value, [k]: v });
  return (
    <fieldset className="space-y-2 rounded border p-3">
      <legend className="px-1 font-medium">Your details, as printed on the passport</legend>
      <p className="text-ink-muted">They are compared once with what we read from the photograph and are not stored.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Surname" value={value.surname} onChange={set("surname")} />
        <TextField label="Given names" value={value.given_names} onChange={set("given_names")} />
        <TextField label="Passport number" value={value.document_number} onChange={set("document_number")} />
        <label className="flex flex-col gap-1">
          <span>Date of birth</span>
          <input type="date" className="rounded border px-2 py-2" value={value.birth_date} onChange={(e) => set("birth_date")(e.target.value)} max={new Date().toISOString().slice(0, 10)} />
        </label>
      </div>
    </fieldset>
  );
}

/** Step 1: choose the document. One choice, nothing else to configure. */
function Choose() {
  const [kind, setKind] = useState<DocKind | null>(null);
  const [country, setCountry] = useState("");
  const open = useCellMutation((api, v: { market: string; route: string }) => api.idv.open(v.market.trim().toUpperCase(), v.route), { invalidate: [MY_IDV_KEY] });
  const market = kind === "AADHAAR" ? AADHAAR_MARKET : country.trim().toUpperCase();
  const ready = kind !== null && /^[A-Z]{2}$/.test(market);
  return (
    <Card title="Choose your document" description="Pick the one you have. You will photograph it on the next step.">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="Document">
          {DOC_OPTIONS.map((k) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={kind === k}
              onClick={() => setKind(k)}
              className={`rounded border-2 p-4 text-left ${kind === k ? "border-brand-500 bg-brand-50" : "border-border"}`}
            >
              <div className="font-medium">{DOC_LABEL[k]}</div>
              <div className="text-sm text-ink-muted">{k === "PASSPORT" ? "Upload the photo page, then take a live photograph. We compare your face with the photograph on the page." : "Upload the front of the card, then take a live photograph. We compare your face with the photograph on the card."}</div>
            </button>
          ))}
        </div>
        {kind === "PASSPORT" ? <TextField label="Country that issued the passport" description="Two letters, for example GB or IN" value={country} onChange={setCountry} /> : null}
        <Button pending={open.isPending} isDisabled={!ready} onPress={() => open.mutate({ market, route: routeFor() })}>
          Continue
        </Button>
        {open.error ? <Failed error={open.error} /> : null}
      </div>
    </Card>
  );
}

/** The answer, always one of: verified, rejected, waiting for the two reviewers, or a photograph that has to be taken again. */
function Answer({ c }: { c: IdvCase }) {
  const state = simpleState(c);
  const reasons = [...new Set(c.reasons.map(applicantText))];
  const what = isFace(c.route) ? "identity check" : DOC_LABEL[docKindOf(c)].toLowerCase();
  if (state === "VERIFIED") return <Banner tone="ok" title="Verified">Your {what} has passed.</Banner>;
  if (state === "REJECTED") {
    return (
      <Banner tone="danger" title="Rejected">
        <div className="space-y-2">
          <p>Your {what} did not pass.</p>
          {reasons.length ? <ul className="list-disc pl-5">{reasons.map((t) => <li key={t}>{t}</li>)}</ul> : null}
        </div>
      </Banner>
    );
  }
  if (state === "WAITING") return <Banner tone="info" title="Sent for review">Your photograph is with two reviewers who work independently of each other. This page updates by itself when they have decided.</Banner>;
  if (state === "RETAKE") {
    return (
      <Banner tone="warn" title="We could not verify this photograph">
        <div className="space-y-2">
          <p>{isFace(c.route) ? "This is not a finding against you: a face could not be found clearly, or it did not match the photograph on the document. Take both photographs again below." : "This is not a finding against you: the photograph or the details could not be read reliably. Take it again below."}</p>
          {reasons.length ? <ul className="list-disc pl-5">{reasons.map((t) => <li key={t}>{t}</li>)}</ul> : null}
        </div>
      </Banner>
    );
  }
  return null;
}

/** Step 2: photograph (or choose) the document and send it. */
function Capture({ c }: { c: IdvCase }) {
  const kind = docKindOf(c);
  const face = isFace(c.route);
  const [file, setFile] = useState<File | null>(null);
  const [live, setLive] = useState<File | null>(null);
  const [agreed, setAgreed] = useState(false);
  const [typed, setTyped] = useState({ name: "", number: "", birth: "" });
  const [details, setDetails] = useState<Declared>(NO_DETAILS);
  const refresh = { invalidate: [MY_IDV_KEY] };
  const send = useCellMutation(
    async (api, f: File) => {
      const { nonce } = await api.idv.nonce(c.case_id);
      return api.idv.documentImage(c.case_id, {
        nonce,
        image_base64: await fileToBase64(f),
        captured_at: new Date().toISOString(),
        ...(isAuto(c.route) ? { declared: trimDetails(details) } : {}),
        ...(face && live ? { selfie_base64: await fileToBase64(live), consent: agreed } : {}),
        ...(face && (typed.name.trim() || typed.number.trim() || typed.birth)
          ? { details: { ...(typed.name.trim() ? { name: typed.name.trim() } : {}), ...(typed.number.trim() ? { number: typed.number.trim() } : {}), ...(typed.birth ? { birth_date: typed.birth } : {}) } }
          : {}),
      });
    },
    { ...refresh, success: face || isAuto(c.route) ? "Received. Checking now" : "Photograph received", onSuccess: () => { setFile(null); setLive(null); setAgreed(false); } },
  );
  const problem = imageProblem(file) ?? (face && live ? imageProblem(live) : null) ?? (isAuto(c.route) ? detailsProblem(details) : null);
  const blocked = problem !== null || (face && (!file || !live || !agreed));
  return (
    <div className="space-y-3">
      <p>
        {face
          ? kind === "PASSPORT"
            ? "Step 1: photograph the passport page with your photo, flat and in good light, the whole page in view."
            : "Step 1: photograph the front of your Aadhaar card, flat and in good light, all four corners in view. You may cover the first eight digits of the number."
          : kind === "PASSPORT"
          ? "Photograph the page with your photo and the two lines of characters at the bottom. Flat, in good light, the whole page in view, nothing covering it."
          : "Photograph the front of your Aadhaar card. Flat, in good light, all four corners in view. You may cover the first eight digits of the number."}
      </p>
      <p className="font-medium">Upload a photograph of the document (recommended):</p>
      <input type="file" accept="image/jpeg,image/png" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Document photograph" />
      <p className="text-ink-muted">Or take it with your camera:</p>
      <DocumentCapture onCaptured={setFile} disabled={send.isPending} />
      {file && !problem ? <p role="status">Ready to send: {file.name} ({Math.round(file.size / 1024)} KB)</p> : null}
      {file && problem ? <p className="text-danger">{problem}</p> : null}
      {face ? (
        <div className="space-y-2">
          <p>Step 2: take a live photograph of your face, looking at the camera, in good light, with only you in the picture.</p>
          <p className="font-medium">Upload a photograph of yourself (recommended):</p>
          <input type="file" accept="image/jpeg,image/png" onChange={(e) => setLive(e.target.files?.[0] ?? null)} aria-label="Live photograph" />
          <p className="text-ink-muted">Or take it with your camera:</p>
          <LiveCapture onCaptured={setLive} disabled={send.isPending} />
          {live ? <p role="status">Live photograph ready: {live.name}</p> : null}
          <fieldset className="space-y-2">
            <legend>Details on the document (optional, and they make the check stronger). Each one is compared with the document photograph and is not kept.</legend>
            <label className="block">Full name<input className="block w-full" value={typed.name} onChange={(e) => setTyped({ ...typed, name: e.target.value })} autoComplete="off" /></label>
            <label className="block">Document number<input className="block w-full" value={typed.number} onChange={(e) => setTyped({ ...typed, number: e.target.value })} autoComplete="off" /></label>
            <label className="block">Date of birth<input type="date" className="block w-full" value={typed.birth} onChange={(e) => setTyped({ ...typed, birth: e.target.value })} /></label>
          </fieldset>
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} className="mt-1" />
            <span>I agree that a program compares my live photograph with the photograph on my document. Neither photograph is kept.</span>
          </label>
        </div>
      ) : null}
      {isAuto(c.route) ? <DetailsForm value={details} onChange={setDetails} /> : null}
      <Button pending={send.isPending} isDisabled={blocked} onPress={() => file && send.mutate(file)}>
        Send for verification
      </Button>
      {send.error ? <Failed error={send.error} /> : null}
      <FaceScore data={send.data} />
    </div>
  );
}

/** The two numbers the face comparison gave, so the person can see it working: how alike the faces are, and how alike they must be. */
function FaceScore({ data }: { data: unknown }) {
  const d = data as { face_similarity?: number | null; face_threshold?: number; details?: Record<string, string> } | undefined;
  if (!d || d.face_threshold === undefined) return null;
  const words: Record<string, string> = { MATCH: "matches the document", MISMATCH: "is not on the document", NOT_READ: "could not be read clearly" };
  const labels: Record<string, string> = { name: "Name", number: "Document number", birth_date: "Date of birth" };
  const lines = Object.entries(d.details ?? {}).map(([k, v]) => <li key={k}>{labels[k] ?? k} {words[v] ?? v}</li>);
  return (
    <div className="space-y-1">
      <FaceLine d={d} />
      {lines.length ? <ul role="status" className="text-ink-muted list-disc pl-5">{lines}</ul> : null}
    </div>
  );
}

function FaceLine({ d }: { d: { face_similarity?: number | null; face_threshold?: number } }) {
  if (d.face_threshold === undefined) return null;
  return d.face_similarity === null || d.face_similarity === undefined ? (
    <p role="status" className="text-ink-muted">Face comparison: no single clear face was found in one of the photographs.</p>
  ) : (
    <p role="status" className="text-ink-muted">
      Face comparison: similarity {d.face_similarity.toFixed(3)}, needed {d.face_threshold.toFixed(3)} ({d.face_similarity >= d.face_threshold ? "matched" : "did not match"}).
    </p>
  );
}

/** The newest check: its answer, and what is still to do. One card, however many earlier attempts exist. */
function Check({ c }: { c: IdvCase }) {
  const state = simpleState(c);
  const again = useCellMutation((api) => api.idv.abandon(c.case_id), { invalidate: [MY_IDV_KEY] });
  return (
    <Card title={isFace(c.route) ? "Identity verification" : `${DOC_LABEL[docKindOf(c)]} verification`}>
      <div className="space-y-4 text-sm">
        <Answer c={c} />
        {state === "SEND" || state === "RETAKE" ? <Capture c={c} /> : null}
        {state !== "VERIFIED" ? (
          <div>
            <Button variant="secondary" pending={again.isPending} onPress={() => again.mutate(undefined)}>
              {state === "REJECTED" ? "Start again" : "Choose a different document"}
            </Button>
            {again.error ? <Failed error={again.error} /> : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
}

export function IdentityProofingPage() {
  const q = useCellQuery(MY_IDV_KEY, (api) => api.idv.cases(), { refetchInterval: 5000 });
  // `?next=` is where a step that sent the person here (a company claim) wants them back once a check is approved.
  const next = safeNext(useSearchParams().get("next"));
  const items = q.data?.items ?? [];
  const approved = items.find((c) => c.decision === "APPROVED");
  // A check opened on a route this page no longer offers (the passport-lines route, the reviewed route, the phone-app chip route) stays on record, and an approved one still counts, but an
  // unfinished one is not shown: it would put an old form in front of the person instead of the choice of document.
  const latest = approved ?? latestCheck(items.filter((c) => isFace(c.route)));
  return (
    <div className="space-y-6">
      <PageHeader title="Verify your identity" description="Choose your passport or your Aadhaar card, photograph it, and we tell you the result." />
      {next && approved ? (
        <AutoContinue to={next} title="Identity verified" once={`idv-approved:${approved.case_id}`}>
          Your identity check is complete. We will take you back to where you were.
        </AutoContinue>
      ) : null}
      {q.isPending && q.fetchStatus !== "idle" ? <LoadingRows rows={3} /> : null}
      {q.isError ? <Failed error={q.error} /> : null}
      {q.data ? latest ? <Check c={latest} /> : <Choose /> : null}
    </div>
  );
}
