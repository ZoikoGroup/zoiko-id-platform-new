"use client";

import { Banner, Button } from "@zoiko/ui";
import { useState } from "react";

import { CameraStep } from "@/features/account/idv/camera-step";
import { fileToBase64 } from "@/features/account/idv/logic";

import { attemptsText, captureProblem, DECLINED_TEXT, EXHAUSTED_TEXT, finishedLive, nextAfter, noticeLines, type CaptureAnswer } from "./live-logic";
import { safeReturn } from "./logic";

type Call = (body: unknown) => Promise<{ ok: boolean; data?: unknown }>;

type Step =
  | { kind: "notice" }
  | { kind: "document" }
  | { kind: "selfie" }
  | { kind: "sending" }
  | { kind: "end"; text: string; back: string | null };

/** The person's side of a live check: the notice first and a plain choice, then the document, then the face, then a truthful end. Camera screens are the identity page's own. The page never shows a
 * score, a result or an accusation, and never stores a photograph: the two files live in memory until they are sent, and a retake discards them. */
export function LiveCheck({ requestedBy, noticeVersion, attemptsLeft, call }: { requestedBy: string; noticeVersion: string | null; attemptsLeft: number; call: Call }) {
  const [step, setStep] = useState<Step>({ kind: "notice" });
  const [doc, setDoc] = useState<File | null>(null);
  const [nonce, setNonce] = useState<string | null>(null);
  const [left, setLeft] = useState(attemptsLeft);
  const [retry, setRetry] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function answerNotice(consented: boolean) {
    setBusy(true);
    setProblem(null);
    try {
      const r = await call({ action: "consent", consented, notice_version: noticeVersion ?? "" });
      if (!r.ok) {
        setProblem("That could not be sent. The link may have ended. Ask the company for a new one.");
        return;
      }
      if (!consented) {
        setStep({ kind: "end", text: DECLINED_TEXT, back: safeReturn((r.data as { return_origin?: string }).return_origin) });
        return;
      }
      await freshNonce();
      setStep({ kind: "document" });
    } catch {
      setProblem("That could not be sent right now. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function freshNonce(): Promise<boolean> {
    const n = await call({ action: "nonce" });
    if (!n.ok) return false;
    const d = n.data as { nonce?: string; attempts_left?: number };
    setNonce(d.nonce ?? null);
    if (typeof d.attempts_left === "number") setLeft(d.attempts_left);
    return Boolean(d.nonce);
  }

  async function send(document: File, selfie: File) {
    const why = captureProblem({ document, selfie });
    if (why || !nonce) {
      setProblem(why ?? "This could not be started. Please begin again.");
      return;
    }
    setStep({ kind: "sending" });
    setProblem(null);
    try {
      const r = await call({ action: "capture", nonce, captured_at: new Date().toISOString(), image_base64: await fileToBase64(document), selfie_base64: await fileToBase64(selfie) });
      if (!r.ok) {
        setProblem("That could not be sent. Please try again. If it keeps happening, the link may have ended.");
        setStep({ kind: "document" });
        setDoc(null);
        await freshNonce();
        return;
      }
      const a = r.data as CaptureAnswer;
      const next = nextAfter(a);
      if (next.kind === "done") {
        setStep({ kind: "end", text: next.exhausted ? EXHAUSTED_TEXT(requestedBy) : finishedLive(requestedBy), back: safeReturn(a.return_origin) });
        return;
      }
      // a retake: the files are dropped, the reason is shown with the tries left, and a new one-time value is fetched
      setRetry(next.text);
      setLeft(next.left);
      setDoc(null);
      await freshNonce();
      setStep({ kind: "document" });
    } catch {
      setProblem("That could not be sent right now. Please try again.");
      setDoc(null);
      setStep({ kind: "document" });
    }
  }

  if (step.kind === "end") {
    return (
      <section className="space-y-4">
        <h1 className="text-2xl font-bold tracking-tight text-ink">Thank you</h1>
        <p role="status" className="text-ink-muted">
          {step.text}
        </p>
        {step.back ? (
          <a className="inline-block text-brand-600 underline" href={step.back} rel="noreferrer noopener">
            Back to {requestedBy}
          </a>
        ) : null}
      </section>
    );
  }
  if (step.kind === "sending") {
    return (
      <p role="status" className="text-ink-muted">
        We have your photographs and are checking them. This takes a few seconds.
      </p>
    );
  }
  if (step.kind === "document" || step.kind === "selfie") {
    const isDoc = step.kind === "document";
    return (
      <section className="space-y-3">
        <p className="text-sm font-medium text-ink-muted" aria-live="polite">
          Step {isDoc ? "1" : "2"} of 2 · {attemptsText(left)}
        </p>
        {retry && isDoc ? (
          <Banner tone="info" title="Let us try again">
            {retry}
          </Banner>
        ) : null}
        {problem ? (
          <p role="alert" className="text-sm text-danger-fg">
            {problem}
          </p>
        ) : null}
        {isDoc ? (
          <CameraStep
            key="document"
            title="Photograph your identity document"
            hint="Put the document on a plain surface and fit it inside the frame. The photograph of your face on it must be clear."
            facing="environment"
            frame="document"
            allowUpload
            uploadLabel="Upload a photograph of the document"
            deviceLabel="Take a photograph of the document with your device's camera app"
            onBack={() => setStep({ kind: "notice" })}
            onUse={(f) => {
              setDoc(f);
              setRetry(null);
              setStep({ kind: "selfie" });
            }}
          />
        ) : (
          <CameraStep
            key="selfie"
            title="Take a photograph of your face"
            hint="Face the camera in good light. Take off glasses or a hat. Only you should be in the picture."
            facing="user"
            frame="face"
            deviceLabel="Take a photograph of your face with your device's camera app"
            onBack={() => setStep({ kind: "document" })}
            onUse={(f) => {
              if (doc) void send(doc, f);
            }}
          />
        )}
      </section>
    );
  }
  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight text-ink">Before we start</h1>
      <ul className="list-disc space-y-2 pl-5 text-ink-muted">
        {noticeLines(requestedBy).map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-3">
        <Button isDisabled={busy} pending={busy} onPress={() => void answerNotice(true)}>
          I understand and want to continue
        </Button>
        <Button variant="secondary" isDisabled={busy} onPress={() => void answerNotice(false)}>
          No thanks
        </Button>
      </div>
      {problem ? (
        <p role="alert" className="text-sm text-danger-fg">
          {problem}
        </p>
      ) : null}
    </section>
  );
}
