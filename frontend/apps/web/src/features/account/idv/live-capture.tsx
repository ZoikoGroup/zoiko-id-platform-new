"use client";

import { Banner, Button } from "@zoiko/ui";
import { useCallback, useEffect, useRef, useState } from "react";

/** The person's live photograph: the front camera, a preview, and one button that takes the picture. If the browser cannot open a camera, the person chooses a photograph from their device
 * instead. Nothing here judges the photograph: the platform finds the face, and a face it cannot find is a retake, never a failure. The photograph is kept in memory until it is sent. */
export function LiveCapture({ onCaptured, disabled }: { onCaptured: (file: File) => void; disabled?: boolean }) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [on, setOn] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const stop = useCallback(() => {
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
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "user" }, width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false });
      stream.current = s;
      setOn(true);
      requestAnimationFrame(() => {
        const v = video.current;
        if (v) {
          v.srcObject = s;
          void v.play().catch(() => undefined);
        }
      });
    } catch {
      setProblem("The camera could not be opened. Allow it for this site, or choose a photograph from your device instead.");
    }
  }

  function take() {
    const v = video.current;
    if (!v || !v.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = v.videoWidth;
    canvas.height = v.videoHeight;
    canvas.getContext("2d")?.drawImage(v, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (blob) onCaptured(new File([blob], "live-photo.jpg", { type: "image/jpeg" }));
        stop();
      },
      "image/jpeg",
      0.92,
    );
  }

  return (
    <div className="space-y-2">
      {problem ? <Banner tone="warn" title="Camera">{problem}</Banner> : null}
      {on ? (
        <div className="space-y-2">
          <video ref={video} className="max-h-64 rounded border" playsInline muted aria-label="Camera preview" />
          <div className="flex gap-2">
            <Button onPress={take} isDisabled={disabled}>
              Take the photograph
            </Button>
            <Button variant="secondary" onPress={stop}>
              Close the camera
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="secondary" onPress={() => void start()} isDisabled={disabled}>
          Open the camera
        </Button>
      )}
    </div>
  );
}
