"use client";

import { Banner, Button, Card, Checkbox, Dialog, PageHeader, TextField } from "@zoiko/ui";
import { AlertTriangle, Check, Copy, Download, KeyRound, RefreshCw } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

/** Download as a plain-text file, built in memory; nothing is written to web storage. */
function downloadCodes(codes: string[]) {
  const text = [
    "Zoiko iD recovery codes",
    `Generated ${new Date().toISOString()}`,
    "",
    "Each code works once. Generating new codes makes these stop working.",
    "",
    ...codes,
    "",
  ].join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "zoiko-id-recovery-codes.txt";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Stops an account recovery the person did not start. The id comes from the notification they were sent. */
function CancelRecoveryCard() {
  const [id, setId] = useState("");
  const cancel = useCellMutation((api, recoveryId: string) => api.me.cancelRecovery(recoveryId), {
    success: "That recovery attempt was stopped. It cannot be used to sign in.",
    onSuccess: () => setId(""),
  });
  const value = id.trim();
  return (
    <Card
      title="A recovery attempt you do not recognise"
      description="If you were told that someone started recovering your account and it was not you, stop it here."
    >
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (value) cancel.mutate(value);
        }}
      >
        <TextField
          label="Recovery attempt ID"
          mono
          value={id}
          onChange={setId}
          placeholder="from the notification"
          className="min-w-64 flex-1"
          description="Stopping it is final: whoever started it has to start again."
        />
        <Button type="submit" variant="danger" isDisabled={!value} isPending={cancel.isPending} className="mb-5">
          Stop this recovery
        </Button>
      </form>
      {cancel.isError ? <ApiErrorView error={cancel.error} /> : null}
    </Card>
  );
}

export function RecoveryCodesPage() {
  const [confirming, setConfirming] = useState(false);
  // Held only in component state: gone on navigation or reload, never cached or stored.
  const [codes, setCodes] = useState<string[] | null>(null);
  const [copied, setCopied] = useState(false);
  const [stored, setStored] = useState(false);

  const { api, protect } = useRuntime();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // Called directly (not through React Query) so the codes never enter a cache; `protect`
  // still turns STEP_UP_REQUIRED into the passkey check.
  async function generate() {
    setPending(true);
    setError(null);
    try {
      const r = await protect(() => api.me.issueRecoveryCodes());
      setConfirming(false);
      setCopied(false);
      setStored(false);
      setCodes(r.codes);
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }

  function done() {
    setCodes(null);
    setStored(false);
    setCopied(false);
  }

  return (
    <>
      <PageHeader
        title="Recovery codes"
        description="One-time codes that let you get back into your account if you lose every passkey."
        actions={
          codes ? null : (
            <Button icon={<RefreshCw aria-hidden className="size-4" />} onPress={() => setConfirming(true)}>
              Generate new codes
            </Button>
          )
        }
      />

      {codes ? (
        <Card title="Your new recovery codes" description="Save them somewhere safe, away from your devices.">
          <div className="space-y-4">
            <Banner tone="warn" icon={AlertTriangle} title="Shown once" live="polite">
              These codes can&apos;t be shown again. Your previous codes no longer work.
            </Banner>
            <ol aria-label="Recovery codes" className="grid gap-2 sm:grid-cols-2">
              {codes.map((c, i) => (
                <li key={c} className="flex items-center gap-3 rounded-control border border-border bg-canvas px-3 py-2">
                  <span aria-hidden className="w-5 text-right text-xs text-ink-subtle tabular">
                    {i + 1}.
                  </span>
                  <code className="font-mono text-sm tracking-wide text-ink">{c}</code>
                </li>
              ))}
            </ol>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                icon={copied ? <Check aria-hidden className="size-4 text-ok-fg" /> : <Copy aria-hidden className="size-4" />}
                onPress={() => void navigator.clipboard?.writeText(codes.join("\n")).then(() => setCopied(true))}
              >
                {copied ? "Copied" : "Copy all"}
              </Button>
              <Button variant="secondary" icon={<Download aria-hidden className="size-4" />} onPress={() => downloadCodes(codes)}>
                Download .txt
              </Button>
            </div>
            <Checkbox isSelected={stored} onChange={setStored}>
              I have saved these codes. I understand they won&apos;t be shown again.
            </Checkbox>
            <div className="flex justify-end">
              <Button isDisabled={!stored} onPress={done}>
                Done
              </Button>
            </div>
          </div>
        </Card>
      ) : (
        <Card title="How recovery codes work">
          <div className="space-y-3 text-sm text-ink-muted">
            <p>
              If you lose access to all of your passkeys, enter one of these codes on the{" "}
              <strong className="text-ink">Recover your account</strong> page to set up a new passkey.
            </p>
            <ul className="list-disc space-y-1 ps-5">
              <li>Each code works once.</li>
              <li>Generating a new set replaces the old one: earlier codes stop working immediately.</li>
              <li>Store them offline, for example printed or in a password manager, not in email or notes on this device.</li>
              <li>
                Administrator and specially protected accounts also need identity re-verification and approval; a code alone isn&apos;t
                enough for them.
              </li>
            </ul>
            <p className="flex items-center gap-2 text-xs text-ink-subtle">
              <KeyRound aria-hidden className="size-4" />
              Codes aren&apos;t shown again after you leave this page. Generating them needs a fresh passkey check.
            </p>
          </div>
        </Card>
      )}

      {codes ? null : <CancelRecoveryCard />}

      <Dialog
        isOpen={confirming}
        onOpenChange={(o) => {
          if (!o && !pending) {
            setConfirming(false);
            setError(null);
          }
        }}
        title="Generate new recovery codes?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={pending} onPress={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button pending={pending} onPress={() => void generate()}>
              Generate codes
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <p>Any recovery codes you generated before stop working as soon as the new ones are created.</p>
          <p>You may be asked to confirm with your passkey first.</p>
          {error ? <ApiErrorView error={error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
