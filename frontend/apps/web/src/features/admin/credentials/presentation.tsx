"use client";

import type { Presentation, PresentationCreated, PresentationIn } from "@zoiko/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { Banner, Button, Card, CopyId, DateTime, JsonView, KeyValues, Select, StatusBadge, TextField, TextLink } from "@zoiko/ui";
import { QrCode, RefreshCw, ScanLine } from "lucide-react";
import { useState, type FormEvent } from "react";

import { LookupForm, listInput, useCellMutation, useCellQuery } from "@/lib/cell";

import { AdminErrorView, AdminQueryView, UUID_RE, required, uuidInput } from "../identity-providers/shared";
import { CONFIGURATIONS_KEY } from "./configurations";

const POLL_MS = 3000;
type ResponseMode = PresentationIn["response_mode"];

/** Create an OpenID4VP request for a wallet, then watch its status. */
export function PresentationPanel() {
  const configs = useCellQuery(CONFIGURATIONS_KEY, (api) => api.credentials.configurations());
  const [configurationId, setConfigurationId] = useState<string | null>(null);
  const [purpose, setPurpose] = useState("");
  const [claims, setClaims] = useState("");
  const [jurisdiction, setJurisdiction] = useState("");
  const [subject, setSubject] = useState("");
  const [mode, setMode] = useState<ResponseMode>("direct_post.jwt");
  const [localError, setLocalError] = useState<unknown>(null);
  const [created, setCreated] = useState<PresentationCreated | null>(null);
  const [watchId, setWatchId] = useState<string | null>(null);

  const request = useCellMutation((api, body: PresentationIn) => api.credentials.requestPresentation(body), {
    success: "Presentation request created",
    onSuccess: (r) => {
      setCreated(r);
      setWatchId(r.presentation_id);
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setLocalError(null);
    request.reset();
    try {
      if (!configurationId) throw new Error("Choose an active credential configuration.");
      const c = listInput(claims);
      if (!c.length) throw new Error("Request at least one claim.");
      if (c.length > 30) throw new Error("At most 30 claims.");
      const j = jurisdiction.trim().toUpperCase();
      if (!/^[A-Z]{2}$/.test(j)) throw new Error("Jurisdiction must be a two-letter country code.");
      request.mutate({
        configuration_id: configurationId,
        purpose: required(purpose, "Purpose"),
        claims: c,
        jurisdiction: j,
        subject_principal_id: subject.trim() ? uuidInput(subject, "Expected subject") : null,
        response_mode: mode,
      });
    } catch (err) {
      setLocalError(err);
    }
  }

  const active = (configs.data?.items ?? []).filter((c) => c.status === "ACTIVE");
  const authorizationRequest = typeof created?.authorization_request === "string" ? created.authorization_request : null;
  const expiresAt = typeof created?.expires_at === "string" ? created.expires_at : null;

  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Card
        title={
          <span className="flex items-center gap-2">
            <QrCode aria-hidden className="size-4 text-ink-muted" /> Request a presentation
          </span>
        }
        description="Create an OpenID4VP request a wallet can answer. This app must be registered as a relying party for the purpose."
      >
        <form onSubmit={submit} noValidate className="space-y-4">
          <Select
            label="Credential configuration"
            isRequired
            value={configurationId}
            onChange={setConfigurationId}
            options={active.map((c) => ({ id: c.configuration_id, label: c.credential_type, description: c.format }))}
            placeholder={configs.isPending ? "Loading…" : active.length ? "Select…" : "No active configurations"}
          />
          {configs.isError ? <AdminErrorView error={configs.error} /> : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField label="Purpose" isRequired mono value={purpose} onChange={setPurpose} placeholder="age_verification" />
            <TextField label="Jurisdiction" isRequired mono value={jurisdiction} onChange={setJurisdiction} placeholder="GB" />
            <TextField
              label="Claims"
              isRequired
              mono
              value={claims}
              onChange={setClaims}
              placeholder="age_over_18"
              description="Comma separated; must be allowed by the registration."
              className="sm:col-span-2"
            />
            <TextField
              label="Expected subject (optional)"
              mono
              value={subject}
              onChange={setSubject}
              description="Principal ID the credential must belong to."
            />
            <Select
              label="Response mode"
              value={mode}
              onChange={(v) => setMode(v as ResponseMode)}
              options={[
                { id: "direct_post.jwt", label: "direct_post.jwt", description: "Encrypted response (HAIP). Recommended." },
                { id: "direct_post", label: "direct_post", description: "Unencrypted form post." },
              ]}
            />
          </div>
          {localError ?? request.error ? <AdminErrorView error={localError ?? request.error} /> : null}
          <div className="flex justify-end">
            <Button type="submit" pending={request.isPending}>
              Create request
            </Button>
          </div>
        </form>

        {created ? (
          <div role="status" className="mt-5 space-y-3 rounded-lg border border-border bg-canvas/60 p-4">
            <div className="text-sm font-semibold text-ink">Request ready</div>
            <KeyValues
              columns={1}
              items={[
                ["Presentation", <CopyId key="p" value={created.presentation_id} label="presentation" short={false} />],
                [
                  "Wallet link",
                  authorizationRequest ? (
                    <span key="w" className="flex flex-col gap-1">
                      <TextLink href={authorizationRequest} className="break-all text-sm">
                        Open in wallet on this device
                      </TextLink>
                      <CopyId value={authorizationRequest} short={false} label="wallet link" />
                    </span>
                  ) : null,
                ],
                ["Request URI", created.request_uri ? <CopyId key="r" value={created.request_uri} short={false} label="request URI" /> : null],
                ["Expires", expiresAt ? <DateTime key="e" value={expiresAt} /> : null],
              ]}
            />
            <p className="text-xs text-ink-muted">
              To use another device, encode the wallet link as a QR code. Claims are released to this app once, on the first read after the wallet
              responds.
            </p>
          </div>
        ) : null}
      </Card>

      <PresentationStatus watchId={watchId} onWatch={setWatchId} />
    </div>
  );
}

function PresentationStatus({ watchId, onWatch }: { watchId: string | null; onWatch: (id: string) => void }) {
  const qc = useQueryClient();
  const key = ["admin", "credentials", "presentation", watchId];
  const cached = qc.getQueryData<Presentation>(key);
  // Stop once the wallet has answered: claims are released once, so a later read would show none.
  const settled = Boolean(cached && cached.status !== "REQUESTED");
  const presentation = useCellQuery(key, (api) => api.credentials.presentation(watchId!), {
    enabled: Boolean(watchId) && !settled,
    refetchInterval: POLL_MS,
  });
  const [badId, setBadId] = useState(false);

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <ScanLine aria-hidden className="size-4 text-ink-muted" /> Presentation status
        </span>
      }
      description="Follows a request until the wallet answers or it expires. Only requests made by this app can be read."
    >
      <div className="space-y-5">
        <LookupForm
          key={watchId ?? "none"}
          label="Presentation ID"
          initial={watchId ?? ""}
          onLookup={(id) => {
            const ok = UUID_RE.test(id);
            setBadId(!ok);
            if (ok) {
              qc.removeQueries({ queryKey: ["admin", "credentials", "presentation", id] });
              onWatch(id);
            }
          }}
        />
        {badId ? <AdminErrorView error={new Error("Presentation ID must be a UUID.")} /> : null}
        {!watchId ? (
          <p className="text-sm text-ink-muted">Create a request or enter a presentation ID to follow it.</p>
        ) : (
          <AdminQueryView query={presentation}>
            {(p) => (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge state={p.status} kind="Presentation" />
                  {p.status === "REQUESTED" ? (
                    <span className="inline-flex items-center gap-1 text-xs text-ink-muted">
                      <RefreshCw aria-hidden className="size-3.5 animate-spin" /> Waiting for the wallet, checking every {POLL_MS / 1000} s
                    </span>
                  ) : null}
                </div>
                <KeyValues
                  items={[
                    ["Issuer", p.issuer ? <span className="break-all font-mono text-xs">{p.issuer}</span> : null],
                    ["Credential status", p.credential_status ? <StatusBadge state={p.credential_status} kind="Credential status" /> : null],
                    ["Reason codes", p.reason_codes?.length ? <span className="font-mono text-xs">{p.reason_codes.join(", ")}</span> : null],
                  ]}
                />
                {p.status === "VERIFIED" ? (
                  p.claims ? (
                    <JsonView value={p.claims} label="Disclosed claims" />
                  ) : (
                    <Banner tone="info">The disclosed claims were already read once; only commitments remain.</Banner>
                  )
                ) : null}
              </div>
            )}
          </AdminQueryView>
        )}
      </div>
    </Card>
  );
}
