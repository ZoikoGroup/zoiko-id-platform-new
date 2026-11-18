"use client";

import { Banner, Button, Card } from "@zoiko/ui";
import { useEffect, useRef, useState } from "react";

import { canCapture, type EngineStanding, type Face, LIVENESS_MS, PROBLEM_TEXT, pickRecorderType, problemOf, toFace, type CaptureProblem } from "./selfie";

type Props = {
  /** What the platform says about the face engine, and the country of the person's document. The camera is not opened unless the engine is approved for that country. */
  engine: EngineStanding | undefined;
  country: string | null;
  /** Called with the two captures, ready for `finishChipRead`. The component keeps nothing after this. */
  onCaptured: (face: Face) => void;
  /** Always offered: a person who cannot use a camera is never stuck (the alternatives route lists approved ways). */
  onSkip: () => void;
};

/** A live selfie: one still and a short liveness capture from the front camera. The camera runs only while this card is open and is released on every exit path. */
export function SelfieCapture({ engine, country, onCaptured, onSkip }: Props) {
  const gate = canCapture(engine, country);
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<CaptureProblem | null>(null);

  function release() {
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    setReady(false);
  }
  useEffect(() => release, []);

  async function open() {
    setProblem(null);
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
      stream.current = s;
      if (video.current) {
        video.current.srcObject = s;
        await video.current.play();
      }
      setReady(true);
    } catch (e) {
      setProblem(problemOf(e));
    }
  }

  async function capture() {
    const el = video.current;
    const s = stream.current;
    if (!el || !s) return;
    const type = pickRecorderType((t) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(t));
    if (!type) return setProblem("NO_RECORDER");
    setBusy(true);
    setProblem(null);
    try {
      const canvas = document.createElement("canvas");
      canvas.width = el.videoWidth || 640;
      canvas.height = el.videoHeight || 480;
      canvas.getContext("2d")?.drawImage(el, 0, 0, canvas.width, canvas.height);
      const still = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
      const chunks: Blob[] = [];
      const rec = new MediaRecorder(s, { mimeType: type });
      rec.ondataavailable = (ev) => ev.data.size > 0 && chunks.push(ev.data);
      const stopped = new Promise<void>((resolve) => (rec.onstop = () => resolve()));
      rec.start();
      await new Promise((r) => setTimeout(r, LIVENESS_MS));
      rec.stop();
      await stopped;
      const probe = new Uint8Array(await (still ?? new Blob()).arrayBuffer());
      const liveness = new Uint8Array(await new Blob(chunks, { type }).arrayBuffer());
      const face = toFace(probe, liveness);
      if (!face.ok) return setProblem(face.problem);
      release();
      onCaptured(face.face);
    } catch (e) {
      setProblem(problemOf(e));
    } finally {
      setBusy(false);
    }
  }

  if (!gate.allowed) {
    return (
      <Card title="Selfie">
        <div className="space-y-3 text-sm">
          <p>{gate.reason}</p>
          <Button variant="secondary" onPress={onSkip}>Continue</Button>
        </div>
      </Card>
    );
  }
  return (
    <Card title="Take a selfie" description="One photo and a three-second capture, used once to check that you are the person on the document and that you are really there. They are not stored by this screen.">
      <div className="space-y-3 text-sm">
        <video ref={video} playsInline muted className={ready ? "w-full max-w-sm rounded-control border border-border" : "hidden"} aria-label="Camera preview" />
        {problem ? <Banner tone="danger" title="Selfie not taken">{PROBLEM_TEXT[problem]}</Banner> : null}
        <div className="flex flex-wrap gap-2">
          {!ready ? <Button onPress={() => void open()}>Turn on the camera</Button> : <Button pending={busy} onPress={() => void capture()}>Take the selfie</Button>}
          <Button variant="secondary" onPress={() => { release(); onSkip(); }}>I cannot use a camera</Button>
        </div>
      </div>
    </Card>
  );
}
