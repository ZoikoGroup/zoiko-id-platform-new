"use client";

import { type IdvCase } from "@zoiko/api-client";
import { Banner, Button, Card, LoadingRows, PageHeader, TextField } from "@zoiko/ui";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { AutoContinue } from "@/components/auto-continue";
import { useCellMutation, useCellQuery } from "@/lib/cell";
import { safeNext } from "@/lib/continue";

import { DocumentCapture } from "./document-capture";
import { Failed } from "./failed";
import { IdentityWizard } from "./identity-wizard";
import { LiveCapture } from "./live-capture";
import {
  applicantText,
  detailsProblem,
  DOC_LABEL,
  docKindOf,
  fileToBase64,
  imageProblem,
  isAuto,
  isFace,
  latestCheck,
  MY_IDV_KEY,
  NO_DETAILS,
  simpleState,
  FACE_ATTEMPTS,
  trimDetails,
  type Declared,
} from "./logic";

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

/** The answer, always one of: verified, rejected, waiting for the two reviewers, or a photograph that has to be taken again. */
function Answer({ c }: { c: IdvCase }) {
  const state = simpleState(c);
  const reasons = [...new Set(c.reasons.map(applicantText))];
  if (state === "RETAKE" && isFace(c.route) && (c.attempts?.CAPTURE ?? 0) >= FACE_ATTEMPTS) {
    return (
      <Banner tone="danger" title="All attempts used">
        <div className="space-y-2">
          <p>The faces did not match closely enough in {FACE_ATTEMPTS} attempts, so this check has ended. This is not a finding about who you are. Try again later with a clearer, face-on photograph in good light, or contact support.</p>
          {reasons.length ? <ul className="list-disc pl-5">{reasons.map((t) => <li key={t}>{t}</li>)}</ul> : null}
        </div>
      </Banner>
    );
  }
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
  if (state === "RESTART") {
    return (
      <Banner tone="warn" title="Please start a new check">
        <p>This check was stopped and cannot continue, so photographs sent to it now will not change the answer. This is not a finding against you. Choose "Choose a different document" below and start again.</p>
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
      });
    },
    { ...refresh, success: face || isAuto(c.route) ? "Received. Checking now" : "Photograph received", onSuccess: () => { setFile(null); setLive(null); setAgreed(false); } },
  );
  const problem = imageProblem(file) ?? (face && live ? imageProblem(live) : null) ?? (isAuto(c.route) ? detailsProblem(details) : null);
  const blocked = problem !== null || (face && (!file || !live || !agreed));
  const used = c.attempts?.CAPTURE ?? 0;
  return (
    <div className="space-y-3">
      {face ? <p className="font-medium">Attempt {Math.min(used + 1, FACE_ATTEMPTS)} of {FACE_ATTEMPTS}</p> : null}
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
const PROBLEM: Record<string, string> = {
  NO_FACE_PROBE: "no face was found in your live photograph. Face the camera, in good light, with nothing covering your face.",
  NO_FACE_REFERENCE: "no face was found in the document photograph. Photograph the card flat and sharp, so the printed photo is clear.",
  MULTIPLE_FACES_PROBE: "more than one face is in your live photograph. Only you should be in the picture.",
  MULTIPLE_FACES_REFERENCE: "the document photograph shows more than one face and the main one could not be chosen. Crop to the card, or photograph the front only.",
  IMAGE_UNREADABLE: "one of the photographs could not be read. Use a JPEG or PNG.",
};

function FaceScore({ data }: { data: unknown }) {
  const d = data as { face_similarity?: number | null; face_threshold?: number; face_problem?: string | null; details?: Record<string, string> } | undefined;
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

function FaceLine({ d }: { d: { face_similarity?: number | null; face_threshold?: number; face_problem?: string | null } }) {
  if (d.face_threshold === undefined) return null;
  return d.face_similarity === null || d.face_similarity === undefined ? (
    <p role="status" className="text-ink-muted">Face comparison: {PROBLEM[d.face_problem ?? ""] ?? "no single clear face was found in one of the photographs."}</p>
  ) : (
    <p role="status" className="text-ink-muted">
      Face comparison: similarity {d.face_similarity.toFixed(3)}, needed {d.face_threshold.toFixed(3)} ({d.face_similarity >= d.face_threshold ? "matched" : "did not match"}).
    </p>
  );
}

/** The newest check: its answer, and what is still to do. One card, however many earlier attempts exist. */
function Check({ c }: { c: IdvCase }) {
  const state = simpleState(c);
  const exhausted = state === "RETAKE" && isFace(c.route) && (c.attempts?.CAPTURE ?? 0) >= FACE_ATTEMPTS;
  const again = useCellMutation((api) => api.idv.abandon(c.case_id), { invalidate: [MY_IDV_KEY] });
  return (
    <Card title={isFace(c.route) ? "Identity verification" : `${DOC_LABEL[docKindOf(c)]} verification`}>
      <div className="space-y-4 text-sm">
        <Answer c={c} />
        {exhausted ? null : state === "SEND" || state === "RETAKE" ? <Capture c={c} /> : null}
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
      <PageHeader title="Verify your identity" description="Photograph an identity document and yourself. We compare the two and tell you the result." />
      {next && approved ? (
        <AutoContinue to={next} title="Identity verified" once={`idv-approved:${approved.case_id}`}>
          Your identity check is complete. We will take you back to where you were.
        </AutoContinue>
      ) : null}
      {q.isPending && q.fetchStatus !== "idle" ? <LoadingRows rows={3} /> : null}
      {q.isError ? <Failed error={q.error} /> : null}
      {q.data ? latest ? <Check c={latest} /> : <IdentityWizard /> : null}
    </div>
  );
}
