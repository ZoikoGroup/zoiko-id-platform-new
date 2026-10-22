"use client";

import type { SsfStream, StreamIn } from "@zoiko/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { Banner, Button, Checkbox, CopyId, Dialog, FieldGroup, KeyValues, OneTimeSecret, RadioCards, Select, TextField, useToast } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, jsonInput, listInput } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { CANONICAL_EVENTS, STREAMS_KEY } from "./shared";

type Direction = StreamIn["direction"];
type Alg = NonNullable<StreamIn["algorithms"]>[number];
type Format = NonNullable<StreamIn["subject_formats"]>[number];

const ALGS: Alg[] = ["ES256", "Ed25519", "RS256", "PS256"];
const FORMATS: { id: Format; label: string }[] = [
  { id: "iss_sub", label: "iss_sub (issuer + subject)" },
  { id: "opaque", label: "opaque (mapped subject IDs)" },
];

const TRUST = [
  { id: "INFORMATIONAL", label: "Informational", description: "Recorded only; never changes security state." },
  { id: "TRUSTED", label: "Trusted", description: "May raise risk and require step-up or re-authentication." },
  { id: "AUTHORITATIVE", label: "Authoritative", description: "May revoke sessions and credentials." },
];

function toggle<T>(list: T[], v: T, on: boolean) {
  return on ? (list.includes(v) ? list : [...list, v]) : list.filter((x) => x !== v);
}

export function CreateStreamDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const { api, protect } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();

  const [direction, setDirection] = useState<Direction>("RECEIVER");
  const [audience, setAudience] = useState("");
  const [issuer, setIssuer] = useState("");
  const [jwks, setJwks] = useState("");
  const [receiverClient, setReceiverClient] = useState("");
  const [events, setEvents] = useState("session.revoked, credential.compromised");
  const [algs, setAlgs] = useState<Alg[]>(["ES256"]);
  const [formats, setFormats] = useState<Format[]>(["iss_sub", "opaque"]);
  const [trust, setTrust] = useState<StreamIn["source_trust"]>("TRUSTED");
  const [fedIssuer, setFedIssuer] = useState("");

  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<string | null>(null);
  // The push credential lives only in component state: never cached, gone once acknowledged.
  const [created, setCreated] = useState<SsfStream | null>(null);

  function reset() {
    setDirection("RECEIVER");
    setAudience("");
    setIssuer("");
    setJwks("");
    setReceiverClient("");
    setEvents("session.revoked, credential.compromised");
    setAlgs(["ES256"]);
    setFormats(["iss_sub", "opaque"]);
    setTrust("TRUSTED");
    setFedIssuer("");
    setError(null);
    setFormError(null);
    setCreated(null);
  }

  function close() {
    if (pending) return;
    reset();
    onClose();
  }

  async function submit() {
    setFormError(null);
    setError(null);
    const evs = listInput(events);
    if (!audience.trim()) return setFormError("Audience is required.");
    if (evs.length === 0) return setFormError("Name at least one event type.");
    if (algs.length === 0) return setFormError("Pick at least one signing algorithm.");
    if (formats.length === 0) return setFormError("Pick at least one subject format.");
    let keys: Record<string, unknown> | undefined;
    if (direction === "RECEIVER") {
      if (!issuer.trim()) return setFormError("A receiver stream pins the transmitter's issuer.");
      try {
        keys = jsonInput(jwks, "Transmitter JWKS");
      } catch (e) {
        return setFormError((e as Error).message);
      }
      if (!keys || !Array.isArray(keys.keys)) return setFormError('Transmitter JWKS must be a JSON object with a "keys" array.');
    } else if (!receiverClient.trim()) {
      return setFormError("A transmitter stream names the OAuth client that polls it.");
    }

    const body: StreamIn = {
      direction,
      audience: audience.trim(),
      events: evs,
      algorithms: algs,
      subject_formats: formats,
      source_trust: trust,
      federation_issuer: fedIssuer.trim() || null,
      ...(direction === "RECEIVER" ? { issuer: issuer.trim(), jwks: keys } : { receiver_client_id: receiverClient.trim() }),
    };
    setPending(true);
    try {
      const r = await protect(() => api.signals.createStream(body));
      await qc.invalidateQueries({ queryKey: STREAMS_KEY });
      toast.notify({ tone: "ok", title: "Stream created", body: "It stays PENDING until you enable it." });
      if (r.push_authorization) setCreated(r);
      else close();
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && !created && close()}
      isDismissable={!created}
      title={created ? "Stream push credential" : "Create SSF stream"}
      description={created ? undefined : "Shared Signals stream (RFC 8935 push / RFC 8936 poll)."}
      size="lg"
      footer={
        created ? undefined : (
          <>
            <Button variant="secondary" isDisabled={pending} onPress={close}>
              Cancel
            </Button>
            <Button pending={pending} onPress={() => void submit()}>
              Create stream
            </Button>
          </>
        )
      }
    >
      {created ? (
        <div className="space-y-4">
          <KeyValues
            items={[
              ["Stream", <CopyId key="s" value={created.stream_id} label="stream ID" />],
              ["Push endpoint", <code key="p" className="font-mono text-xs break-all">/ssf/events/push/{created.stream_ref}</code>],
            ]}
          />
          <p className="text-sm text-ink-muted">
            Give the transmitter this value as the <code className="font-mono">Authorization</code> header for pushes to the endpoint above
            (on the cell API host). Only its hash is stored.
          </p>
          <OneTimeSecret label="Push authorization" secret={created.push_authorization ?? ""} onAcknowledge={close} />
        </div>
      ) : (
        <div className="space-y-5">
          <RadioCards
            label="Direction"
            columns={2}
            value={direction}
            onChange={(v) => setDirection(v as Direction)}
            options={[
              { id: "RECEIVER", label: "Receiver", description: "An external transmitter pushes signed events to this cell." },
              { id: "TRANSMITTER", label: "Transmitter", description: "This cell emits events; a receiver client polls them." },
            ]}
          />
          <TextField label="Audience" isRequired value={audience} onChange={setAudience} mono placeholder="https://receiver.example.com" />
          {direction === "RECEIVER" ? (
            <FieldGroup legend="Transmitter" description="Events are accepted only when signed by these pinned keys for this issuer.">
              <TextField label="Transmitter issuer" isRequired value={issuer} onChange={setIssuer} mono placeholder="https://idp.example.com" />
              <TextField
                label="Transmitter JWKS"
                isRequired
                multiline
                rows={5}
                mono
                value={jwks}
                onChange={setJwks}
                placeholder='{"keys": [{"kty": "EC", "crv": "P-256", "x": "…", "y": "…"}]}'
              />
            </FieldGroup>
          ) : (
            <TextField
              label="Receiver client ID"
              isRequired
              value={receiverClient}
              onChange={setReceiverClient}
              mono
              description="The OAuth client that polls this stream (RFC 8936)."
            />
          )}
          <TextField
            label="Event types"
            isRequired
            multiline
            rows={2}
            mono
            value={events}
            onChange={setEvents}
            description={`Comma or newline separated. Canonical types include ${CANONICAL_EVENTS.slice(0, 6).join(", ")}…`}
          />
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-ink">Signing algorithms</legend>
            <div className="flex flex-wrap gap-4">
              {ALGS.map((a) => (
                <Checkbox key={a} isSelected={algs.includes(a)} onChange={(on) => setAlgs((l) => toggle(l, a, on))}>
                  <span className="font-mono text-xs">{a}</span>
                </Checkbox>
              ))}
            </div>
          </fieldset>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-ink">Subject formats</legend>
            <div className="flex flex-wrap gap-4">
              {FORMATS.map((f) => (
                <Checkbox key={f.id} isSelected={formats.includes(f.id)} onChange={(on) => setFormats((l) => toggle(l, f.id, on))}>
                  {f.label}
                </Checkbox>
              ))}
            </div>
          </fieldset>
          <Select
            label="Source trust"
            options={TRUST}
            value={trust}
            onChange={(v) => setTrust(v as StreamIn["source_trust"])}
            description="Caps how much this stream's events can change security state."
          />
          <TextField label="Federation issuer (optional)" value={fedIssuer} onChange={setFedIssuer} mono />
          {formError ? (
            <Banner tone="danger" title="Check the form" live="polite">
              {formError}
            </Banner>
          ) : null}
          {error ? <ApiErrorView error={error} /> : null}
        </div>
      )}
    </Dialog>
  );
}
