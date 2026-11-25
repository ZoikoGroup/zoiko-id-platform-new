"use client";

import { Button } from "@zoiko/ui";
import { AlertCircle, ArrowLeft, ArrowRight, Camera, RefreshCw, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { analyseFrame, cropToFrame, FRAME_ASPECT, FRAME_WIDTH_SHARE, hintFor } from "./capture-logic";
import { imageProblem } from "./logic";
import { useObjectUrl } from "./use-object-url";

const PREVIEW_WIDTH = 160;

/** One capture screen, the same for the document and the selfie. The camera opens by itself with a guide: a card-shaped frame for the document, an oval for the face.
 * Taking a photograph shows it with Retake and Use this photograph. If the browser cannot open a camera, the person is told and can use their device's camera app, and for
 * the document upload a clear picture. The live hints (light, glare, sharpness) help only; the platform judges the photograph it receives, and nothing here can verify anyone.
 * The camera runs only while this screen is open and is released on every exit. */
export function CameraStep({
  title,
  hint,
  facing,
  frame,
  allowUpload = false,
  uploadLabel,
  deviceLabel,
  busy = false,
  onBack,
  onUse,
}: {
  title: string;
  hint: string;
  facing: "environment" | "user";
  frame: "document" | "face";
  /** The document may be uploaded as a picture; a selfie is taken live (or with the device's camera app). */
  allowUpload?: boolean;
  /** Accessible names of the two file inputs. */
  uploadLabel?: string;
  deviceLabel: string;
  busy?: boolean;
  onBack: () => void;
  onUse: (file: File) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [state, setState] = useState<"starting" | "live" | "unavailable">("starting");
  const [shot, setShot] = useState<File | null>(null);
  const [live, setLive] = useState<{ ok: boolean; text: string }>({ ok: false, text: "Getting the camera ready…" });
  const [problem, setProblem] = useState<string | null>(null);
  const shotUrl = useObjectUrl(shot);

  const stop = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  }, []);

  const start = useCallback(async () => {
    stop();
    setState("starting");
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setState("unavailable");
      return;
    }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false });
      stream.current = s;
      const v = video.current;
      if (v) {
        v.srcObject = s;
        await v.play().catch(() => undefined);
      }
      setState("live");
      if (frame === "document" && v) {
        // a light hint about the page, four times a second at most: the picture is read at a small size and never leaves the browser
        const small = document.createElement("canvas");
        timer.current = setInterval(() => {
          if (!v.videoWidth) return;
          const c = cropToFrame(v.videoWidth, v.videoHeight);
          small.width = PREVIEW_WIDTH;
          small.height = Math.round(PREVIEW_WIDTH / FRAME_ASPECT);
          const g = small.getContext("2d", { willReadFrequently: true });
          if (!g) return;
          g.drawImage(v, c.sx, c.sy, c.sw, c.sh, 0, 0, small.width, small.height);
          setLive(hintFor(analyseFrame(g.getImageData(0, 0, small.width, small.height).data, small.width, small.height)));
        }, 500);
      }
    } catch {
      setState("unavailable");
    }
  }, [facing, frame, stop]);

  useEffect(() => {
    void start();
    return stop;
  }, [start, stop]);

  function take() {
    const v = video.current;
    if (!v || !v.videoWidth) return;
    // the document is cut to the frame the person fitted it in; the face is the whole picture, as the camera saw it (never flipped)
    const c = frame === "document" ? cropToFrame(v.videoWidth, v.videoHeight) : { sx: 0, sy: 0, sw: v.videoWidth, sh: v.videoHeight };
    const canvas = document.createElement("canvas");
    canvas.width = c.sw;
    canvas.height = c.sh;
    canvas.getContext("2d")?.drawImage(v, c.sx, c.sy, c.sw, c.sh, 0, 0, c.sw, c.sh);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        stop();
        setProblem(null);
        setShot(new File([blob], frame === "document" ? "document.jpg" : "live-photo.jpg", { type: "image/jpeg" }));
      },
      "image/jpeg",
      0.92,
    );
  }

  function chosen(file: File | undefined) {
    if (!file) return;
    const why = imageProblem(file);
    if (why) {
      setProblem(why);
      return;
    }
    stop();
    setProblem(null);
    setShot(file);
  }

  function retake() {
    setShot(null);
    setProblem(null);
    void start();
  }

  const mirrored = facing === "user";
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold text-ink">{title}</h3>
        <p className="mt-0.5 text-sm text-ink-muted">{hint}</p>
      </div>

      <div className="relative mx-auto w-full max-w-xl overflow-hidden rounded-2xl bg-black shadow-sm">
        {shot && shotUrl ? (
          // the person checks the photograph with their own eyes: this is the file itself
          // eslint-disable-next-line @next/next/no-img-element
          <img src={shotUrl} alt={frame === "document" ? "Your document photograph" : "Your selfie"} className="block w-full object-contain" />
        ) : (
          <>
            <video ref={video} playsInline muted aria-label={frame === "document" ? "Camera view of your document" : "Camera view of your face"} className={state === "live" ? `block w-full ${mirrored ? "scale-x-[-1]" : ""}` : "hidden"} />
            {state === "live" ? (
              <div aria-hidden="true" className="pointer-events-none absolute inset-0 flex items-center justify-center">
                {frame === "face" ? (
                  <div className="aspect-[3/4] h-[78%] rounded-[50%] border-4 border-white/80 shadow-[0_0_0_9999px_rgba(0,0,0,0.38)]" />
                ) : (
                  <div className="rounded-xl border-4 border-white/80 shadow-[0_0_0_9999px_rgba(0,0,0,0.38)]" style={{ width: `${FRAME_WIDTH_SHARE * 100}%`, aspectRatio: String(FRAME_ASPECT) }} />
                )}
              </div>
            ) : (
              <div className="flex aspect-[4/3] w-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-white/90">
                <Camera aria-hidden className="size-8" />
                {state === "starting" ? (
                  <p role="status">Starting the camera…</p>
                ) : (
                  <p role="status">We could not open your camera here. Allow camera access for this site, or use your device&apos;s camera below.</p>
                )}
              </div>
            )}
          </>
        )}
      </div>

      {state === "live" && !shot && frame === "document" ? (
        <p role="status" aria-live="polite" className={`mx-auto w-fit max-w-xl rounded-full px-3 py-1 text-center text-sm font-medium ${live.ok ? "bg-ok-bg text-ok-fg" : "bg-warn-bg text-warn-fg"}`}>
          {live.text}
        </p>
      ) : null}
      {problem ? (
        <p role="alert" className="mx-auto flex max-w-xl items-start gap-1.5 text-sm font-medium text-danger-fg">
          <AlertCircle aria-hidden className="mt-0.5 size-4 shrink-0" />
          {problem}
        </p>
      ) : null}

      <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between [&>*]:w-full sm:[&>*]:w-auto">
        <Button
          variant="secondary"
          isDisabled={busy}
          icon={<ArrowLeft aria-hidden className="size-4" />}
          onPress={() => {
            stop();
            onBack();
          }}
        >
          Back
        </Button>
        <div className="flex flex-col gap-2 sm:flex-row">
          {shot ? (
            <>
              <Button variant="secondary" isDisabled={busy} icon={<RefreshCw aria-hidden className="size-4" />} onPress={retake}>
                Retake
              </Button>
              <Button isDisabled={busy} icon={<ArrowRight aria-hidden className="size-4" />} onPress={() => onUse(shot)}>
                Use this photo
              </Button>
            </>
          ) : (
            <>
              {allowUpload ? (
                <FileButton ariaLabel={uploadLabel ?? "Upload a picture"} onFile={chosen} icon={<Upload aria-hidden className="size-4" />}>
                  Upload a picture
                </FileButton>
              ) : null}
              {state !== "live" ? (
                <FileButton ariaLabel={deviceLabel} capture={facing} onFile={chosen} icon={<Camera aria-hidden className="size-4" />}>
                  Use device camera
                </FileButton>
              ) : null}
              {state === "unavailable" ? (
                <Button variant="secondary" icon={<RefreshCw aria-hidden className="size-4" />} onPress={() => void start()}>
                  Try camera again
                </Button>
              ) : null}
              <Button isDisabled={state !== "live"} icon={<Camera aria-hidden className="size-4" />} onPress={take}>
                Take photo
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** A button that opens the device's file picker. The real input stays in the page, spread over the button, so a keyboard or screen reader reaches it by its label. */
function FileButton({ ariaLabel, capture, onFile, icon, children }: { ariaLabel: string; capture?: "user" | "environment"; onFile: (f: File | undefined) => void; icon: ReactNode; children: ReactNode }) {
  return (
    <label className="relative inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-border bg-surface px-4 py-2 text-sm font-medium text-ink shadow-sm transition-colors hover:bg-surface-sunken focus-within:outline focus-within:outline-2 focus-within:outline-brand-600">
      {icon}
      {children}
      <input
        type="file"
        accept="image/jpeg,image/png"
        capture={capture}
        aria-label={ariaLabel}
        className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
        onChange={(e) => {
          onFile(e.target.files?.[0]);
          e.target.value = ""; // choosing the same file again must still register
        }}
      />
    </label>
  );
}
