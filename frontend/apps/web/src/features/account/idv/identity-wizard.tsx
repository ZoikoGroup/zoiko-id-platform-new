"use client";

import { Banner, Button, Card, Checkbox, Select, TextField } from "@zoiko/ui";
import { ArrowLeft } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useCellMutation } from "@/lib/cell";

import { COUNTRIES } from "./countries";
import { DocumentCapture } from "./document-capture";
import { CATEGORY_HINT, CATEGORY_LABEL, DOCUMENTS_BY_CATEGORY, IDENTITY_DOCUMENTS, documentLabel, photographAdvice } from "./documents";
import { Failed } from "./failed";
import { LiveCapture } from "./live-capture";
import { FACE_ROUTE, MY_IDV_KEY, fileToBase64 } from "./logic";
import {
  NO_WIZARD_DETAILS,
  STEP_TITLE,
  TOTAL_SCREENS,
  WIZARD_STEPS,
  detailsProblem,
  documentsProblem,
  firstIncomplete,
  fullName,
  marketFor,
  type WizardDetails,
  type WizardStep,
} from "./wizard-logic";

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-border p-3">
      <p className="font-medium text-ink">{title}</p>
      <div className="mt-1 text-sm text-ink-muted">{children}</div>
    </div>
  );
}

/** Identity verification, six screens: what you need, your details, how it works, your documents, review, and the result (the check card, once a case exists).
 * Modeled on the reference product's wizard. The same screens and the same documents for every country. Nothing here can mark anyone verified: the server decides. */
export function IdentityWizard() {
  const [step, setStep] = useState<WizardStep>("INTRO");
  const [details, setDetails] = useState<WizardDetails>(NO_WIZARD_DETAILS);
  const [docType, setDocType] = useState("");
  const [document, setDocument] = useState<File | null>(null);
  const [selfie, setSelfie] = useState<File | null>(null);
  const [agreed, setAgreed] = useState(false);
  const [shown, setShown] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  // keyboard and screen-reader users: focus follows the person to each new screen's heading
  useEffect(() => {
    heading.current?.focus();
  }, [step]);

  const position = WIZARD_STEPS.indexOf(step) + 1;
  const set = (k: keyof WizardDetails) => (v: string) => setDetails((d) => ({ ...d, [k]: v }));
  const next = (to: WizardStep) => {
    setShown(null);
    setStep(to);
  };

  const submit = useCellMutation<void, unknown>(
    async (api) => {
      if (!document || !selfie) throw new Error("photographs missing");
      const opened = await api.idv.open(marketFor(details.country), FACE_ROUTE);
      const { nonce } = await api.idv.nonce(opened.case_id);
      return api.idv.documentImage(opened.case_id, {
        nonce,
        image_base64: await fileToBase64(document),
        captured_at: new Date().toISOString(),
        selfie_base64: await fileToBase64(selfie),
        consent: agreed,
        details: { name: fullName(details), birth_date: details.birth },
      });
    },
    { invalidate: [MY_IDV_KEY], success: "Received. Checking now" },
  );

  const docProblem = documentsProblem({ docType, document, selfie, agreed });

  return (
    <Card>
      <div className="space-y-5">
        <div>
          <p className="text-sm text-ink-muted">
            Step {position} of {TOTAL_SCREENS}
          </p>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-sunken" role="presentation">
            <div className="h-full rounded-full bg-brand-600 transition-all" style={{ width: `${(position / TOTAL_SCREENS) * 100}%` }} />
          </div>
        </div>
        <h2 ref={heading} tabIndex={-1} className="text-xl font-semibold text-ink outline-none">
          {STEP_TITLE[step]}
        </h2>

        {step === "INTRO" ? (
          <div className="space-y-3">
            <Block title="What you will need">
              One government-issued identity document with your photograph (a passport, driving licence, national ID card, Aadhaar, or similar), a camera or a clear picture of it, and about five minutes.
            </Block>
            <Block title="How it works">
              You photograph your document and take a live photograph of your face. We compare the face on the document with your live photograph. If they match, you are verified; if we cannot be sure, a person looks at it.
            </Block>
            <Block title="Privacy">We use your photographs for this check only. Your details are compared once with the document and are not stored.</Block>
            <Button onPress={() => next("DETAILS")}>Start</Button>
          </div>
        ) : null}

        {step === "DETAILS" ? (
          <div className="space-y-4">
            <p className="text-sm text-ink-muted">Type your name and date of birth exactly as they appear on your document.</p>
            <div className="grid gap-3 sm:grid-cols-3">
              <TextField label="Given name" isRequired value={details.given} onChange={set("given")} autoComplete="given-name" />
              <TextField label="Middle names (optional)" value={details.middle} onChange={set("middle")} autoComplete="additional-name" />
              <TextField label="Family name" isRequired value={details.family} onChange={set("family")} autoComplete="family-name" />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-sm">
                <span>
                  Date of birth <span aria-hidden>*</span>
                </span>
                <input
                  type="date"
                  className="rounded border px-2 py-2"
                  value={details.birth}
                  onChange={(e) => set("birth")(e.target.value)}
                  max={new Date().toISOString().slice(0, 10)}
                  required
                  autoComplete="bday"
                />
              </label>
              <Select
                label="Country you live in (optional)"
                value={details.country || null}
                onChange={set("country")}
                options={COUNTRIES}
                placeholder="Choose…"
                description="For the record only. It does not change which documents you can use."
              />
            </div>
            {shown && step === "DETAILS" ? <Banner tone="warn" title="Check your details">{shown}</Banner> : null}
            <div className="flex gap-2">
              <Button variant="secondary" icon={<ArrowLeft aria-hidden className="size-4" />} onPress={() => next("INTRO")}>
                Back
              </Button>
              <Button
                onPress={() => {
                  const p = detailsProblem(details);
                  if (p) return setShown(p);
                  next("HOW");
                }}
              >
                Continue
              </Button>
            </div>
          </div>
        ) : null}

        {step === "HOW" ? (
          <div className="space-y-3">
            <Block title="1. Photograph your document">Flat, in good light, all of it in view. A photograph or a picture file (JPEG or PNG) both work.</Block>
            <Block title="2. Take a live photograph of your face">Look at the camera, in good light, with only you in the picture.</Block>
            <Block title="3. We decide, or a person does">
              We compare the face on your document with your live photograph, and you see the result straight away. If we cannot be sure, a person looks at it. Neither photograph is kept when the platform decides.
            </Block>
            <Banner tone="info" title="Before the camera check">
              By continuing you agree that a program compares your live photograph with the photograph on your document. This check does not prove a document is genuine, and it is not a check that you are physically present.
            </Banner>
            <div className="flex gap-2">
              <Button variant="secondary" icon={<ArrowLeft aria-hidden className="size-4" />} onPress={() => next("DETAILS")}>
                Back
              </Button>
              <Button onPress={() => next("DOCUMENTS")}>Continue</Button>
            </div>
          </div>
        ) : null}

        {step === "DOCUMENTS" ? (
          <div className="space-y-5">
            <fieldset className="space-y-2">
              <legend className="font-medium text-ink">{CATEGORY_LABEL.identity}</legend>
              <p className="text-sm text-ink-muted">{CATEGORY_HINT.identity} Pick the one you will photograph.</p>
              <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Identity document">
                {IDENTITY_DOCUMENTS.map((d) => (
                  <button
                    key={d.value}
                    type="button"
                    role="radio"
                    aria-checked={docType === d.value}
                    onClick={() => setDocType(d.value)}
                    className={`rounded border-2 px-3 py-2 text-left text-sm ${docType === d.value ? "border-brand-500 bg-brand-50" : "border-border"}`}
                  >
                    {d.label}
                  </button>
                ))}
              </div>
            </fieldset>

            {docType ? (
              <div className="space-y-2">
                <p className="font-medium text-ink">Photograph of your {documentLabel(docType).toLowerCase()}</p>
                <p className="text-sm text-ink-muted">Photograph {photographAdvice(docType)}.</p>
                <input type="file" accept="image/jpeg,image/png" aria-label="Document photograph" onChange={(e) => setDocument(e.target.files?.[0] ?? null)} />
                <p className="text-sm text-ink-muted">Or take it with your camera:</p>
                <DocumentCapture onCaptured={setDocument} disabled={submit.isPending} />
                {document ? <p role="status" className="text-sm">Ready: {document.name} ({Math.round(document.size / 1024)} KB)</p> : null}
              </div>
            ) : null}

            <div className="space-y-2">
              <p className="font-medium text-ink">Live photograph of your face</p>
              <p className="text-sm text-ink-muted">Look at the camera, in good light, with only you in the picture.</p>
              <input type="file" accept="image/jpeg,image/png" aria-label="Live photograph" onChange={(e) => setSelfie(e.target.files?.[0] ?? null)} />
              <p className="text-sm text-ink-muted">Or take it with your camera:</p>
              <LiveCapture onCaptured={setSelfie} disabled={submit.isPending} />
              {selfie ? <p role="status" className="text-sm">Ready: {selfie.name}</p> : null}
            </div>

            <Checkbox isSelected={agreed} onChange={setAgreed}>
              I agree that a program compares my live photograph with the photograph on my document. Neither photograph is kept when the platform decides.
            </Checkbox>

            <Block title="Other documents">
              {CATEGORY_LABEL.address} and {CATEGORY_LABEL.other.toLowerCase()} (for example {DOCUMENTS_BY_CATEGORY.address[0]?.label.toLowerCase()}, {DOCUMENTS_BY_CATEGORY.other[0]?.label.toLowerCase()}) do not prove who you are, so they are not part
              of this check. If something else is needed, we will ask for it.
            </Block>

            {shown && step === "DOCUMENTS" ? <Banner tone="warn" title="Not ready yet">{shown}</Banner> : null}
            <div className="flex gap-2">
              <Button variant="secondary" icon={<ArrowLeft aria-hidden className="size-4" />} onPress={() => next("HOW")}>
                Back
              </Button>
              <Button
                onPress={() => {
                  if (docProblem) return setShown(docProblem);
                  next("REVIEW");
                }}
              >
                Continue
              </Button>
            </div>
          </div>
        ) : null}

        {step === "REVIEW" ? (
          <div className="space-y-4">
            <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-ink-muted">Name</dt>
                <dd>{fullName(details)}</dd>
              </div>
              <div>
                <dt className="text-ink-muted">Date of birth</dt>
                <dd>{details.birth}</dd>
              </div>
              <div>
                <dt className="text-ink-muted">Country you live in</dt>
                <dd>{COUNTRIES.find((c) => c.id === details.country)?.label ?? "Not given"}</dd>
              </div>
              <div>
                <dt className="text-ink-muted">Document</dt>
                <dd>{docType ? documentLabel(docType) : "-"}</dd>
              </div>
              <div>
                <dt className="text-ink-muted">Photographs</dt>
                <dd>{document && selfie ? `${document.name}, ${selfie.name}` : "-"}</dd>
              </div>
            </dl>
            <Banner tone="info" title="What happens next">
              We compare the two photographs and show you the outcome when it is ready; if we cannot be sure, two people look at it. Finishing these steps is not the same as being verified, and this is not a finding about who you are.
            </Banner>
            {submit.error ? <Failed error={submit.error} /> : null}
            <div className="flex gap-2">
              <Button variant="secondary" isDisabled={submit.isPending} icon={<ArrowLeft aria-hidden className="size-4" />} onPress={() => next("DOCUMENTS")}>
                Back
              </Button>
              <Button
                pending={submit.isPending}
                onPress={() => {
                  const back = firstIncomplete({ details, docType, document, selfie, agreed });
                  if (back) return next(back);
                  submit.mutate();
                }}
              >
                Submit for verification
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
