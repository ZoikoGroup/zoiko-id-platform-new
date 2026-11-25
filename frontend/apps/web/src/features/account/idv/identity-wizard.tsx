"use client";

import { Banner, Button, Card, Checkbox, Select, TextField } from "@zoiko/ui";
import { ArrowLeft, ArrowRight, Camera, Check, Clock, FileText, IdCard, Lock, ScanFace, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useCellMutation } from "@/lib/cell";

import { CameraStep } from "./camera-step";
import { COUNTRIES } from "./countries";
import { IDENTITY_DOCUMENTS, documentLabel, photographAdvice } from "./documents";
import { Failed } from "./failed";
import { FACE_ROUTE, MY_IDV_KEY, fileToBase64 } from "./logic";
import { useObjectUrl } from "./use-object-url";
import {
  NO_WIZARD_DETAILS,
  STEP_TITLE,
  TOTAL_SCREENS,
  WIZARD_STEPS,
  detailsProblem,
  firstIncomplete,
  fullName,
  marketFor,
  type WizardDetails,
  type WizardStep,
} from "./wizard-logic";

/** The labels of the screens in the progress bar, in order. The last, the result, is the check card that appears once a case exists. */
const STEP_NAMES = ["Start", "Details", "Guide", "Document", "Photo", "Selfie", "Review", "Result"];

/** Where the person is: numbered steps with the finished ones ticked. On a phone only the bar and "Step n of 8" show; the list would not fit. */
function Progress({ position }: { position: number }) {
  return (
    <div>
      <p className="text-sm font-medium text-ink-muted">
        Step {position} of {TOTAL_SCREENS}
      </p>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-sunken sm:hidden" role="presentation">
        <div className="h-full rounded-full bg-brand-600 transition-all" style={{ width: `${(position / TOTAL_SCREENS) * 100}%` }} />
      </div>
      <ol className="mt-3 hidden items-center sm:flex" aria-label="Progress">
        {STEP_NAMES.map((name, i) => {
          const n = i + 1;
          const state = n < position ? "done" : n === position ? "current" : "todo";
          return (
            <li key={name} className={`flex items-center ${i < STEP_NAMES.length - 1 ? "flex-1" : ""}`} aria-current={state === "current" ? "step" : undefined}>
              <span
                className={`grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold ${
                  state === "done" ? "bg-brand-600 text-white" : state === "current" ? "bg-brand-50 text-brand-700 ring-2 ring-brand-600" : "bg-sunken text-ink-subtle"
                }`}
              >
                {state === "done" ? <Check aria-hidden className="size-4" /> : n}
              </span>
              <span className={`ml-2 whitespace-nowrap text-xs font-medium ${state === "todo" ? "text-ink-subtle" : "text-ink"}`}>
                {name}
                <span className="sr-only">{state === "done" ? " (done)" : state === "current" ? " (current step)" : ""}</span>
              </span>
              {i < STEP_NAMES.length - 1 ? <span aria-hidden className={`mx-2 h-px flex-1 ${n < position ? "bg-brand-600" : "bg-border"}`} /> : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function InfoCard({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
      <span aria-hidden className="grid size-9 place-items-center rounded-lg bg-brand-50 text-brand-700">
        {icon}
      </span>
      <h3 className="mt-3 text-sm font-semibold text-ink">{title}</h3>
      <p className="mt-1 text-sm leading-relaxed text-ink-muted">{children}</p>
    </div>
  );
}

function Numbered({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-full bg-brand-50 text-sm font-semibold text-brand-700">
        {n}
      </span>
      <div>
        <p className="text-sm font-semibold text-ink">{title}</p>
        <p className="mt-0.5 text-sm leading-relaxed text-ink-muted">{children}</p>
      </div>
    </li>
  );
}

/** Back on the left, the way forward on the right. On a phone they stack with the way forward first, full width, where a thumb reaches it. */
function Nav({ children }: { children: ReactNode }) {
  return <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between [&>*]:w-full sm:[&>*]:w-auto">{children}</div>;
}

function Thumb({ file, caption, retake, onRetake }: { file: File | null; caption: string; retake: string; onRetake: () => void }) {
  const url = useObjectUrl(file);
  return (
    <figure className="min-w-0">
      <div className="grid aspect-[4/3] place-items-center overflow-hidden rounded-xl border border-border bg-sunken">
        {url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={url} alt={caption} className="h-full w-full object-cover" />
        ) : (
          <span className="text-sm text-ink-subtle">Not added</span>
        )}
      </div>
      <figcaption className="mt-1.5 flex items-center justify-between gap-2 text-xs text-ink-muted">
        <span className="truncate">{caption}</span>
        <button type="button" aria-label={retake} onClick={onRetake} className="shrink-0 font-semibold text-brand-700 hover:underline">
          Retake
        </button>
      </figcaption>
    </figure>
  );
}

/** Identity verification, one task at a time, as in the reference product: what you need, your details, how it works, which document, a photograph of it (the camera opens with
 * a guide), a selfie (an oval guide), and a review with the agreement before anything is sent. The same screens and the same documents for every country. Nothing here can mark
 * anyone verified: the server decides. */
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

  const docName = docType ? documentLabel(docType) : "document";

  return (
    <Card>
      <div className="mx-auto max-w-3xl space-y-6">
        <Progress position={position} />
        <h2 ref={heading} tabIndex={-1} className="text-2xl font-semibold tracking-tight text-ink outline-none">
          {STEP_TITLE[step]}
        </h2>

        {step === "INTRO" ? (
          <div className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-3">
              <InfoCard icon={<FileText className="size-5" />} title="What you will need">
                One government-issued identity document with your photograph (a passport, driving licence, national ID card, Aadhaar, or similar), a camera or a clear picture of it, and about five minutes.
              </InfoCard>
              <InfoCard icon={<ScanFace className="size-5" />} title="How it works">
                You photograph your document and take a live photograph of your face. We compare the face on the document with your live photograph. If they match, you are verified; if we cannot be sure, a person looks at it.
              </InfoCard>
              <InfoCard icon={<Lock className="size-5" />} title="Privacy">
                We use your photographs for this check only. Your details are compared once with the document and are not stored.
              </InfoCard>
            </div>
            <p className="flex items-center gap-1.5 text-sm text-ink-muted">
              <Clock aria-hidden className="size-4" />
              Takes about five minutes. You can stop at any time.
            </p>
            <Nav>
              <span className="hidden sm:block" />
              <Button icon={<ArrowRight aria-hidden className="size-4" />} onPress={() => next("DETAILS")}>
                Start
              </Button>
            </Nav>
          </div>
        ) : null}

        {step === "DETAILS" ? (
          <div className="space-y-5">
            <p className="text-sm text-ink-muted">Type your name and date of birth exactly as they appear on your document.</p>
            <div className="grid gap-4 sm:grid-cols-3">
              <TextField label="Given name" isRequired value={details.given} onChange={set("given")} autoComplete="given-name" />
              <TextField label="Middle names (optional)" value={details.middle} onChange={set("middle")} autoComplete="additional-name" />
              <TextField label="Family name" isRequired value={details.family} onChange={set("family")} autoComplete="family-name" />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-sm font-medium text-ink">
                <span>
                  Date of birth <span aria-hidden>*</span>
                </span>
                <input
                  type="date"
                  className="h-10 rounded-md border border-border bg-surface px-3 text-sm font-normal text-ink shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600"
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
            <Nav>
              <Button variant="secondary" icon={<ArrowLeft aria-hidden className="size-4" />} onPress={() => next("INTRO")}>
                Back
              </Button>
              <Button
                icon={<ArrowRight aria-hidden className="size-4" />}
                onPress={() => {
                  const p = detailsProblem(details);
                  if (p) return setShown(p);
                  next("HOW");
                }}
              >
                Continue
              </Button>
            </Nav>
          </div>
        ) : null}

        {step === "HOW" ? (
          <div className="space-y-5">
            <ol className="space-y-4 rounded-xl border border-border bg-surface p-5 shadow-sm">
              <Numbered n={1} title="Photograph your document">
                Flat, in good light, all of it in view. Your camera opens with a guide, or you can upload a clear picture (JPEG or PNG).
              </Numbered>
              <Numbered n={2} title="Take a quick selfie">
                Look at the camera, in good light, with only you in the picture. Fit your face inside the oval.
              </Numbered>
              <Numbered n={3} title="We decide, or a person does">
                We compare the face on your document with your live photograph, and you see the result straight away. If we cannot be sure, a person looks at it. Neither photograph is kept when the platform decides.
              </Numbered>
            </ol>
            <Banner tone="info" title="Before the camera check">
              You will be asked to agree that a program compares your live photograph with the photograph on your document. This check does not prove a document is genuine, and it is not a check that you are physically present.
            </Banner>
            <Nav>
              <Button variant="secondary" icon={<ArrowLeft aria-hidden className="size-4" />} onPress={() => next("DETAILS")}>
                Back
              </Button>
              <Button icon={<Camera aria-hidden className="size-4" />} onPress={() => next("DOCUMENT")}>
                Continue
              </Button>
            </Nav>
          </div>
        ) : null}

        {step === "DOCUMENT" ? (
          <div className="space-y-5">
            <fieldset className="space-y-3">
              <legend className="text-base font-semibold text-ink">Which document will you use?</legend>
              <p className="text-sm text-ink-muted">Choose a document with your photograph on it. Only the face on it is compared with your selfie.</p>
              <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Identity document">
                {IDENTITY_DOCUMENTS.map((d) => {
                  const on = docType === d.value;
                  return (
                    <button
                      key={d.value}
                      type="button"
                      role="radio"
                      aria-checked={on}
                      onClick={() => setDocType(d.value)}
                      className={`flex items-center justify-between gap-2 rounded-lg border-2 px-3.5 py-3 text-left text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600 ${
                        on ? "border-brand-600 bg-brand-50 text-brand-700" : "border-border bg-surface text-ink hover:border-brand-600/50 hover:bg-surface-sunken"
                      }`}
                    >
                      <span className="flex items-center gap-2.5">
                        <IdCard aria-hidden className={`size-4 shrink-0 ${on ? "text-brand-700" : "text-ink-subtle"}`} />
                        {d.label}
                      </span>
                      <span aria-hidden className={`grid size-5 shrink-0 place-items-center rounded-full ${on ? "bg-brand-600 text-white" : "border border-border"}`}>
                        {on ? <Check className="size-3.5" /> : null}
                      </span>
                    </button>
                  );
                })}
              </div>
            </fieldset>
            <Nav>
              <Button variant="secondary" icon={<ArrowLeft aria-hidden className="size-4" />} onPress={() => next("HOW")}>
                Back
              </Button>
              <Button isDisabled={!docType} icon={<Camera aria-hidden className="size-4" />} onPress={() => next("DOCUMENT_PHOTO")}>
                Continue
              </Button>
            </Nav>
          </div>
        ) : null}

        {step === "DOCUMENT_PHOTO" ? (
          <CameraStep
            key="document"
            title={`Photograph of your ${docName.toLowerCase()}`}
            hint={`Photograph ${photographAdvice(docType)}. You can also upload a clear picture of it.`}
            facing="environment"
            frame="document"
            allowUpload
            uploadLabel="Document photograph"
            deviceLabel="Document photograph from your device camera"
            busy={submit.isPending}
            onBack={() => next("DOCUMENT")}
            onUse={(f) => {
              setDocument(f);
              next("SELFIE");
            }}
          />
        ) : null}

        {step === "SELFIE" ? (
          <CameraStep
            key="selfie"
            title="A clear photograph of your face"
            hint="Face the camera in good light, looking straight ahead. Remove sunglasses or a hat, and make sure only you are in the picture."
            facing="user"
            frame="face"
            deviceLabel="Live photograph"
            busy={submit.isPending}
            onBack={() => next("DOCUMENT_PHOTO")}
            onUse={(f) => {
              setSelfie(f);
              next("REVIEW");
            }}
          />
        ) : null}

        {step === "REVIEW" ? (
          <div className="space-y-5">
            <p className="text-sm text-ink-muted">Check that your face is clearly visible in both pictures, then submit. We compare the face on your {docName.toLowerCase()} with your selfie.</p>
            <div className="grid gap-4 sm:grid-cols-2">
              <section aria-label="Your photographs" className="grid grid-cols-2 gap-3 sm:col-span-2">
                <Thumb file={document} caption="Identity document" retake="Retake the document photograph" onRetake={() => next("DOCUMENT_PHOTO")} />
                <Thumb file={selfie} caption="Selfie" retake="Retake the selfie" onRetake={() => next("SELFIE")} />
              </section>
              <section aria-label="Your details" className="rounded-xl border border-border bg-surface p-4 shadow-sm sm:col-span-2 sm:p-5">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-ink">Your details</h3>
                  <button type="button" onClick={() => next("DETAILS")} className="text-xs font-semibold text-brand-700 hover:underline">
                    Change
                  </button>
                </div>
                <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-4">
                  <div>
                    <dt className="text-xs text-ink-subtle">Name</dt>
                    <dd className="font-medium text-ink">{fullName(details)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-ink-subtle">Date of birth</dt>
                    <dd className="font-medium text-ink">{details.birth}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-ink-subtle">Country you live in</dt>
                    <dd className="font-medium text-ink">{COUNTRIES.find((c) => c.id === details.country)?.label ?? "Not given"}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-ink-subtle">Document</dt>
                    <dd className="font-medium text-ink">{docType ? documentLabel(docType) : "-"}</dd>
                  </div>
                </dl>
              </section>
            </div>

            <div className={`rounded-xl border p-4 shadow-sm transition-colors sm:p-5 ${agreed ? "border-brand-600 bg-brand-50" : "border-border bg-surface"}`}>
              <div className="flex items-start gap-3">
                <ShieldCheck aria-hidden className="mt-0.5 size-5 shrink-0 text-brand-700" />
                <Checkbox isSelected={agreed} onChange={setAgreed}>
                  I agree that a program compares my live photograph with the photograph on my document. Neither photograph is kept when the platform decides.
                </Checkbox>
              </div>
            </div>
            <Banner tone="info" title="What happens next">
              We compare the two photographs and show you the outcome when it is ready; if we cannot be sure, two people look at it. Finishing these steps is not the same as being verified, and this is not a finding about who you are.
            </Banner>
            {submit.error ? <Failed error={submit.error} /> : null}
            <Nav>
              <Button variant="secondary" isDisabled={submit.isPending} icon={<ArrowLeft aria-hidden className="size-4" />} onPress={() => next("SELFIE")}>
                Back
              </Button>
              <div className="flex flex-col items-stretch gap-1 sm:items-end">
                <Button
                  pending={submit.isPending}
                  isDisabled={!agreed}
                  icon={<ShieldCheck aria-hidden className="size-4" />}
                  onPress={() => {
                    const back = firstIncomplete({ details, docType, document, selfie });
                    if (back) return next(back);
                    submit.mutate();
                  }}
                >
                  Submit for verification
                </Button>
                {!agreed ? <span className="text-xs text-ink-subtle">Tick the agreement above to submit.</span> : null}
              </div>
            </Nav>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
