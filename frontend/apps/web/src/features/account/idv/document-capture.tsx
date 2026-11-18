"use client";

import { Banner, Button } from "@zoiko/ui";
import { useCallback, useEffect, useRef, useState } from "react";

import { analyseFrame, cropToFrame, FRAME_ASPECT, FRAME_WIDTH_SHARE, hintFor } from "./capture-logic";

const PREVIEW_WIDTH = 160;

/** A guided camera for the document page: a frame the page fits, live hints about light, glare and sharpness, and a photograph cropped to the frame. The camera runs only while this is open and is released on every exit. If
 * the browser cannot open a camera the person is told and chooses a photograph from their device instead. The hints help; the platform judges the photograph it receives. */
export function DocumentCapture({ onCaptured, disabled }: { onCaptured: (file: File) => void; disabled?: boolean }) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [on, setOn] = useState(false);
  const [hint, setHint] = useState<{ ok: boolean; text: string }>({ ok: false, text: "Starting the camera…" });
  const [problem, setProblem] = useState<string | null>(null);

  const stop = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    setOn(false);
  }, []);
  useEffect(() => stop, [stop]);

  async function start() {
    setProblem(null);
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setProblem("This browser cannot open the camera. Choose a photograph from your device instead.");
      return;
    }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false });
      stream.current = s;
      setOn(true);
      requestAnimationFrame(async () => {
        const v = video.current;
        if (!v) return;
        v.srcObject = s;
        await v.play().catch(() => undefined);
        const small = document.createElement("canvas");
        timer.current = setInterval(() => {
          if (!v.videoWidth) return;
          const c = cropToFrame(v.videoWidth, v.videoHeight);
          small.width = PREVIEW_WIDTH;
          small.height = Math.round(PREVIEW_WIDTH / FRAME_ASPECT);
          const g = small.getContext("2d", { willReadFrequently: true });
          if (!g) return;
          g.drawImage(v, c.sx, c.sy, c.sw, c.sh, 0, 0, small.width, small.height);
          setHint(hintFor(analyseFrame(g.getImageData(0, 0, small.width, small.height).data, small.width, small.height)));
        }, 500);
      });
    } catch {
      setProblem("The camera could not be opened. Allow it for this site, or choose a photograph from your device instead.");
    }
  }

  function take() {
    const v = video.current;
    if (!v || !v.videoWidth) return;
    const c = cropToFrame(v.videoWidth, v.videoHeight);
    const canvas = document.createElement("canvas");
    canvas.width = c.sw;
    canvas.height = c.sh;
    canvas.getContext("2d")?.drawImage(v, c.sx, c.sy, c.sw, c.sh, 0, 0, c.sw, c.sh);
    canvas.toBlob(
      (blob) => {
        if (blob) onCaptured(new File([blob], "document.jpg", { type: "image/jpeg" }));
        stop();
      },
      "image/jpeg",
      0.92,
    );
  }

  if (!on) {
    return (
      <div className="space-y-2">
        <Button variant="secondary" isDisabled={disabled} onPress={start}>Use the camera</Button>
        {problem ? <Banner tone="warn" title="Camera">{problem}</Banner> : null}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="relative mx-auto max-w-xl overflow-hidden rounded border border-border bg-black">
        <video ref={video} playsInline muted className="block w-full" aria-label="Camera view" />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded border-2 border-dashed border-white"
          style={{ width: `${FRAME_WIDTH_SHARE * 100}%`, aspectRatio: String(FRAME_ASPECT) }}
        />
      </div>
      <p role="status" aria-live="polite" className={hint.ok ? "text-ok" : "text-warn"}>{hint.text}</p>
      <div className="flex gap-2">
        <Button onPress={take}>Take the photograph</Button>
        <Button variant="secondary" onPress={stop}>Cancel</Button>
      </div>
    </div>
  );
}
