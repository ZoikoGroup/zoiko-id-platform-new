"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Banner, Button, Checkbox, Dialog, TextField, useToast } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { ENDPOINTS_KEY, EVENT_TYPES_KEY, shortEvent } from "./shared";

/** The events a company that only verifies its own customers needs: the result of a check, and a check that ended without one. The catalogue names them com.zoiko.id.verification.api.session_decided.v1 and
 * ...session_closed.v1, so the match is on the short name. */
const IDENTITY_EVENT_PREFIX = "verification.api.";
const isIdentityEvent = (type: string): boolean => shortEvent(type).startsWith(IDENTITY_EVENT_PREFIX);

export function CreateEndpointDialog({ isOpen, onClose, identityOnly = false }: { isOpen: boolean; onClose: () => void; identityOnly?: boolean }) {
  const { api, protect } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();
  const types = useCellQuery(EVENT_TYPES_KEY, (a) => a.webhooks.eventTypes(), { enabled: isOpen });

  const [url, setUrl] = useState("");
  // null means "the person has not touched the list": in identity-only mode that is every identity verification event, ticked for them.
  const [picked, setPicked] = useState<string[] | null>(null);
  const offered = (types.data?.items ?? []).filter((t) => !identityOnly || isIdentityEvent(t.type));
  const selected = picked ?? (identityOnly ? offered.map((t) => t.type) : []);
  const setSelected = (next: string[] | ((l: string[]) => string[])) => setPicked(typeof next === "function" ? next(selected) : next);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<string | null>(null);

  function close() {
    if (pending) return;
    setUrl("");
    setPicked(null);
    setError(null);
    setFormError(null);
    onClose();
  }

  async function submit() {
    setFormError(null);
    setError(null);
    if (!url.trim().startsWith("https://")) return setFormError("The URL must start with https://.");
    if (selected.length === 0) return setFormError("Choose at least one event type.");
    setPending(true);
    try {
      await protect(() => api.webhooks.createEndpoint({ url: url.trim(), event_types: selected }));
      await qc.invalidateQueries({ queryKey: ENDPOINTS_KEY });
      toast.notify({ tone: "ok", title: "Endpoint registered", body: "Verify it to start receiving events." });
      close();
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title="Register webhook endpoint"
      description={
        identityOnly
          ? "Your server receives the result of each identity verification here. The endpoint stays pending until it echoes a signed verification challenge."
          : "The endpoint stays pending until it echoes a signed verification challenge."
      }
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={pending} onPress={close}>
            Cancel
          </Button>
          <Button pending={pending} onPress={() => void submit()}>
            Register endpoint
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <TextField
          label="Endpoint URL"
          isRequired
          mono
          value={url}
          onChange={setUrl}
          placeholder="https://hooks.example.com/zoiko"
          description="HTTPS only, a DNS name (not an IP address), standard port. Private and internal addresses are refused."
        />
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-ink">{identityOnly ? "Identity verification events" : "Events"}</legend>
          <QueryView query={types}>
            {() => (
              <div className="grid gap-2 sm:grid-cols-2">
                {offered.map((t) => (
                  <Checkbox
                    key={t.type}
                    isSelected={selected.includes(t.type)}
                    onChange={(on) => setSelected((l) => (on ? [...l, t.type] : l.filter((x) => x !== t.type)))}
                  >
                    <span className="flex flex-col">
                      <span className="font-mono text-xs">{shortEvent(t.type)}</span>
                      <span className="text-2xs text-ink-subtle">{t.description}</span>
                    </span>
                  </Checkbox>
                ))}
              </div>
            )}
          </QueryView>
        </fieldset>
        {formError ? (
          <Banner tone="danger" title="Check the form" live="polite">
            {formError}
          </Banner>
        ) : null}
        {error ? <ApiErrorView error={error} /> : null}
      </div>
    </Dialog>
  );
}
