"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { VerifyApiApplication, VerifyApiCredential, VerifyApiIssued } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, CopyId, DataTable, Dialog, EmptyState, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Check, Copy, KeyRound, Plus, RefreshCw, ShieldOff } from "lucide-react";
import { useState } from "react";

import { AppSettings } from "./app-settings";
import { CasesCard } from "./cases";
import { ReviewCard } from "./review";
import { CredentialApprovals } from "./credential-approvals";
import { assertionSnippet, KEY_CREDENTIAL_NOTE, parsePublicKey } from "./credential-options-logic";
import { GoLive } from "./go-live";
import { TestLab } from "./test-lab";
import { ResultsCard } from "./results";
import { UsageCard } from "./usage-card";
import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import {
  DEFAULT_SCOPES,
  SCOPES,
  SECRET_WARNING,
  canIssue,
  credentialState,
  firstOriginProblem,
  nameProblem,
  parseOrigins,
  scopeLabel,
  tokenSnippet,
  whoamiSnippet,
} from "./verify-api-logic";

const KEY = ["admin", "developers", "verify-api"];
const BASE = "https://YOUR-API-ADDRESS";

const when = (iso: string | null): string => (iso ? new Date(iso).toLocaleString("en-GB") : "Never");

/** The applications a company's server uses to call the Verify API, and the credentials of each. A secret is shown once, when it is created or rotated, and the page cannot be closed on it until the person
 * confirms they copied it: the cell keeps only a hash and can never show it again. Only the sandbox is available until the production gate has passed. */
export function VerifyApiSection() {
  const list = useCellQuery([...KEY, "applications"], (api) => api.verifyApi.applications());
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState<"SANDBOX" | "PRODUCTION" | null>(null);
  const columns: Column<VerifyApiApplication>[] = [
    { key: "n", header: "Application", pinned: true, render: (a) => <span className="font-medium">{a.name}</span>, sortValue: (a) => a.name },
    { key: "e", header: "Environment", render: (a) => <Badge tone={a.environment === "SANDBOX" ? "info" : "warn"}>{a.environment === "SANDBOX" ? "Sandbox" : "Production"}</Badge> },
    { key: "s", header: "Status", render: (a) => <StatusBadge state={a.status} kind="Application" /> },
    { key: "sc", header: "Can do", render: (a) => `${a.scopes.length} of ${SCOPES.length}` },
    { key: "c", header: "Created", render: (a) => when(a.created_at), sortValue: (a) => a.created_at },
  ];
  return (
    <div className="space-y-4">
      <Banner tone="info" title="Verify your own customers from your own server">
        Create an application, get a client ID and a secret, and your server exchanges them for a short-lived token. Build and test in the sandbox, where no real person or document is checked. Production
        opens after your company is verified and your integration is certified.{" "}
        <a className="font-medium text-brand-700 underline" href="/docs/verify-api" target="_blank" rel="noreferrer noopener">
          Read the documentation
        </a>
        .
      </Banner>
      <GoLive onCreateProduction={() => setCreating("PRODUCTION")} />
      <QueryView query={list}>
        {(d) => (
          <Card
            title="Applications"
            description="Each application has its own credentials and the scopes you give it. Open one to see and manage its credentials."
            actions={
              <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating("SANDBOX")}>
                Create application
              </Button>
            }
            padded={false}
          >
            <DataTable<VerifyApiApplication>
              caption="Verify API applications"
              columns={columns}
              rows={d.items}
              rowKey={(a) => a.application_id}
              onRowAction={(a) => setOpen(a.application_id)}
              empty={
                <EmptyState title="No applications yet" icon={KeyRound}>
                  Create your first application to get credentials for the sandbox.
                </EmptyState>
              }
            />
          </Card>
        )}
      </QueryView>
      <ResultsCard />
      <ReviewCard />
      <CasesCard />
      <UsageCard />
      {open ? <ApplicationDetail id={open} onClose={() => setOpen(null)} /> : null}
      {creating ? <CreateApplicationDialog environment={creating} onClose={() => setCreating(null)} onCreated={(a) => setOpen(a.application_id)} /> : null}
    </div>
  );
}

function CreateApplicationDialog({ onClose, onCreated, environment }: { onClose: () => void; onCreated: (a: VerifyApiApplication) => void; environment: "SANDBOX" | "PRODUCTION" }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [origins, setOrigins] = useState("");
  const [scopes, setScopes] = useState<string[]>(DEFAULT_SCOPES);
  const [advanced, setAdvanced] = useState(false);
  const list = parseOrigins(origins);
  const nameErr = name.trim() === "" ? null : nameProblem(name);
  const originErr = firstOriginProblem(list);
  const create = useCellMutation((api) => api.verifyApi.createApplication({ name: name.trim(), environment, scopes, redirect_origins: list }), {
    success: "Application created",
    onSuccess: (a) => {
      void qc.invalidateQueries({ queryKey: KEY });
      onCreated(a);
      onClose();
    },
  });
  const ready = name.trim() !== "" && !nameErr && !originErr && scopes.length > 0;
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title={environment === "PRODUCTION" ? "Create a production application" : "Create an application"}
      description="It starts in the sandbox. You can add credentials as soon as it exists."
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!ready} onPress={() => create.mutate(undefined)}>
            Create application
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField label="Application name" value={name} onChange={setName} placeholder="Checkout website" description="For you and your team. It is not shown to your customers." errorMessage={nameErr ?? undefined} />
        <TextField
          label="Addresses your customers return to (optional)"
          value={origins}
          onChange={setOrigins}
          multiline
          rows={3}
          placeholder={"https://app.example.com"}
          description="One per line. After verifying, a customer is sent back only to an address listed here. Each must be an exact https address."
          errorMessage={originErr ?? undefined}
        />
        <div>
          <button type="button" className="text-sm font-medium text-brand-700 hover:underline" aria-expanded={advanced} onClick={() => setAdvanced((v) => !v)}>
            {advanced ? "Hide what this application can do" : `What this application can do (${scopes.length} of ${SCOPES.length})`}
          </button>
          {advanced ? (
            <fieldset className="mt-2 space-y-2 rounded-lg border border-border p-3">
              <legend className="sr-only">What this application can do</legend>
              {SCOPES.map((s) => (
                <Checkbox
                  key={s.id}
                  isSelected={scopes.includes(s.id)}
                  onChange={(on) => setScopes((cur) => (on ? [...cur, s.id] : cur.filter((x) => x !== s.id)))}
                  description={s.help}
                >
                  {s.label}
                </Checkbox>
              ))}
              {scopes.length === 0 ? <p className="text-xs text-danger-fg">Choose at least one.</p> : null}
            </fieldset>
          ) : null}
        </div>
        {create.error ? <ApiErrorView error={create.error} title="The application was not created" /> : null}
      </div>
    </Dialog>
  );
}

function ApplicationDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const detail = useCellQuery([...KEY, "application", id], (api) => api.verifyApi.application(id));
  const [issued, setIssued] = useState<VerifyApiIssued | null>(null);
  const [rotating, setRotating] = useState<VerifyApiCredential | null>(null);
  const [keying, setKeying] = useState(false);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: KEY });
  };
  const issue = useCellMutation((api) => api.verifyApi.issueCredential(id), { onSuccess: (r) => (setIssued(r), refresh()) });
  const revoke = useCellMutation((api, cid: string) => api.verifyApi.revokeCredential(id, cid), { success: "Credential revoked. Its tokens stopped working.", onSuccess: refresh });
  const change = useCellMutation((api, c: "suspend" | "resume" | "retire") => api.verifyApi.changeApplication(id, c), { success: "Application updated", onSuccess: refresh });
  return (
    <QueryView query={detail}>
      {(a) => {
        const columns: Column<VerifyApiCredential>[] = [
          { key: "c", header: "Client ID", pinned: true, render: (c) => <CopyId value={c.client_id} short={false} label="client ID" /> },
          {
            key: "k",
            header: "Signs in with",
            render: (c) =>
              c.auth_method === "PRIVATE_KEY_JWT" ? (
                <span className="text-xs text-ink-muted">
                  Private key <span className="font-mono">{c.key_id}</span>
                </span>
              ) : (
                <span className="font-mono text-xs text-ink-muted">Secret zps_…{c.secret_hint}</span>
              ),
          },
          { key: "s", header: "State", render: (c) => <Badge tone={credentialState(c).tone}>{credentialState(c).label}</Badge> },
          { key: "u", header: "Last used", render: (c) => when(c.last_used_at) },
          { key: "x", header: "Valid until", render: (c) => when(c.expires_at) },
          {
            key: "a",
            header: "",
            align: "end",
            render: (c) =>
              c.status === "ACTIVE" ? (
                <span className="flex justify-end gap-2">
                  {a.environment === "SANDBOX" ? (
                    <Button size="xs" variant="secondary" icon={<RefreshCw aria-hidden className="size-3.5" />} onPress={() => setRotating(c)}>
                      Rotate
                    </Button>
                  ) : null}
                  <Button size="xs" variant="secondary" icon={<ShieldOff aria-hidden className="size-3.5" />} isDisabled={revoke.isPending} onPress={() => revoke.mutate(c.credential_id)}>
                    Revoke
                  </Button>
                </span>
              ) : c.status === "ROTATING" ? (
                <Button size="xs" variant="secondary" isDisabled={revoke.isPending} onPress={() => revoke.mutate(c.credential_id)}>
                  End now
                </Button>
              ) : null,
          },
        ];
        const firstActive = a.credentials.find((c) => c.status === "ACTIVE");
        const first = firstActive?.client_id ?? "<your client id>";
        const byKey = firstActive?.auth_method === "PRIVATE_KEY_JWT";
        return (
          <Card
            title={a.name}
            description={`${a.environment === "SANDBOX" ? "Sandbox" : "Production"} application. ${a.status === "ACTIVE" ? "" : `It is ${a.status.toLowerCase()}, so its credentials do not work.`}`}
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge state={a.status} kind="Application" />
                {a.status === "ACTIVE" ? (
                  <Button size="sm" variant="secondary" isDisabled={change.isPending} onPress={() => change.mutate("suspend")}>
                    Suspend
                  </Button>
                ) : null}
                {a.status === "SUSPENDED" ? (
                  <Button size="sm" variant="secondary" isDisabled={change.isPending} onPress={() => change.mutate("resume")}>
                    Resume
                  </Button>
                ) : null}
                {a.status !== "RETIRED" ? (
                  <Button size="sm" variant="secondary" isDisabled={change.isPending} onPress={() => change.mutate("retire")}>
                    Retire
                  </Button>
                ) : null}
                <Button size="sm" variant="secondary" onPress={onClose}>
                  Close
                </Button>
              </div>
            }
          >
            <div className="space-y-5">
              <section aria-label="Credentials" className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-ink">Credentials</h3>
                  <div className="flex flex-wrap gap-2">
                    {a.environment === "SANDBOX" ? (
                      <>
                        <Button size="sm" icon={<Plus aria-hidden className="size-4" />} isDisabled={!canIssue(a.status) || issue.isPending} pending={issue.isPending} onPress={() => issue.mutate(undefined)}>
                          Create credential
                        </Button>
                        <Button size="sm" variant="secondary" icon={<KeyRound aria-hidden className="size-4" />} isDisabled={!canIssue(a.status)} onPress={() => setKeying(true)}>
                          Create key credential
                        </Button>
                      </>
                    ) : (
                      <span className="text-xs text-ink-muted">Production credentials need approval: see below.</span>
                    )}
                  </div>
                </div>
                <p className="text-xs text-ink-muted">
                  A credential is a client ID and a secret. The secret is shown once, when you create it, and we keep only a fingerprint of it. To change a secret, rotate it: the new one works at once and the
                  old one keeps working for a short, chosen time so you can deploy without downtime.
                </p>
                <p className="text-xs text-ink-muted">{KEY_CREDENTIAL_NOTE}</p>
                <DataTable<VerifyApiCredential>
                  caption="Credentials"
                  columns={columns}
                  rows={a.credentials}
                  rowKey={(c) => c.credential_id}
                  empty={
                    <EmptyState title="No credentials yet" icon={KeyRound} compact>
                      Create a credential to call the API from your server.
                    </EmptyState>
                  }
                />
                {issue.error ? <ApiErrorView error={issue.error} title="The credential was not created" onRetry={() => issue.reset()} /> : null}
                {revoke.error ? <ApiErrorView error={revoke.error} title="The credential was not revoked" onRetry={() => revoke.reset()} /> : null}
                {change.error ? <ApiErrorView error={change.error} title="The application was not changed" onRetry={() => change.reset()} /> : null}
              </section>

              <section aria-label="What it can do" className="space-y-1">
                <h3 className="text-sm font-semibold text-ink">What this application can do</h3>
                <ul className="flex flex-wrap gap-1.5">
                  {a.scopes.map((s) => (
                    <li key={s}>
                      <Badge tone="neutral">{scopeLabel(s)}</Badge>
                    </li>
                  ))}
                </ul>
                {a.redirect_origins.length ? <p className="pt-1 text-xs text-ink-muted">Customers return to: {a.redirect_origins.join(", ")}</p> : <p className="pt-1 text-xs text-ink-muted">No return addresses are set.</p>}
              </section>

              {a.environment === "PRODUCTION" ? (
                <CredentialApprovals
                  app={a}
                  onIssued={(r) => {
                    setIssued(r);
                    refresh();
                  }}
                />
              ) : null}

              <AppSettings key={a.updated_at} app={a} />

              <TestLab applicationId={id} active={a.status === "ACTIVE"} />

              <section aria-label="First call" className="space-y-2">
                <h3 className="text-sm font-semibold text-ink">Try it from your server</h3>
                <p className="text-xs text-ink-muted">Use your Zoiko iD API address in place of YOUR-API-ADDRESS. Replace the placeholders with your own values. Never put the secret in a web page or a mobile app.</p>
                <pre className="overflow-x-auto rounded-lg border border-border bg-surface-sunken p-3 text-xs">{byKey ? assertionSnippet(BASE, first) : tokenSnippet(BASE, first)}</pre>
                <pre className="overflow-x-auto rounded-lg border border-border bg-surface-sunken p-3 text-xs">{whoamiSnippet(BASE)}</pre>
              </section>
            </div>
            {issued ? <SecretDialog issued={issued} title="Your new credential" onDone={() => setIssued(null)} /> : null}
            {keying ? (
              <KeyDialog
                applicationId={id}
                onClose={() => setKeying(false)}
                onIssued={(r) => {
                  setKeying(false);
                  setIssued(r);
                  refresh();
                }}
              />
            ) : null}
            {rotating ? (
              <RotateDialog
                credential={rotating}
                applicationId={id}
                onClose={() => setRotating(null)}
                onRotated={(r) => {
                  setRotating(null);
                  setIssued(r);
                  refresh();
                }}
              />
            ) : null}
          </Card>
        );
      }}
    </QueryView>
  );
}

function RotateDialog({ credential, applicationId, onClose, onRotated }: { credential: VerifyApiCredential; applicationId: string; onClose: () => void; onRotated: (r: VerifyApiIssued) => void }) {
  const [hours, setHours] = useState("24");
  const [keyText, setKeyText] = useState("");
  const byKey = credential.auth_method === "PRIVATE_KEY_JWT";
  const key = parsePublicKey(keyText);
  const n = Number(hours);
  const valid = /^\d{1,3}$/.test(hours) && n >= 1 && n <= 168 && (!byKey || key.jwk !== null);
  const rotate = useCellMutation((api) => api.verifyApi.rotateCredential(applicationId, credential.credential_id, n, byKey ? (key.jwk ?? undefined) : undefined), { onSuccess: onRotated });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !rotate.isPending && onClose()}
      title="Rotate this credential"
      description="You get a new secret now. The current one keeps working for the time below, so you can deploy the new one without an outage, and then stops."
      footer={
        <>
          <Button variant="secondary" isDisabled={rotate.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={rotate.isPending} isDisabled={!valid} icon={<RefreshCw aria-hidden className="size-4" />} onPress={() => rotate.mutate(undefined)}>
            Rotate
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField
          label="Keep the current secret working for (hours)"
          value={hours}
          onChange={setHours}
          inputMode="numeric"
          description="Between 1 and 168 hours (7 days). 24 is a good start."
          errorMessage={valid ? undefined : "Enter a whole number of hours from 1 to 168."}
        />
        {byKey ? (
          <TextField
            label="The new public key"
            value={keyText}
            onChange={setKeyText}
            multiline
            rows={5}
            placeholder={'{"kty":"OKP","crv":"Ed25519","x":"..."}'}
            description="Paste the public half of your new key as a JSON Web Key. Never paste a private key."
            errorMessage={key.problem ?? undefined}
          />
        ) : null}
        <p className="text-xs text-ink-muted">Current credential: {credential.client_id}</p>
        {rotate.error ? <ApiErrorView error={rotate.error} title="The credential was not rotated" /> : null}
      </div>
    </Dialog>
  );
}

/** The one place a secret appears. It cannot be closed until the person confirms they copied it, because there is no way to see it again. */
function SecretDialog({ issued, title, onDone }: { issued: VerifyApiIssued; title: string; onDone: () => void }) {
  const secret = issued.client_secret;
  const [confirmed, setConfirmed] = useState(secret === undefined);
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && confirmed && onDone()}
      title={title}
      description={secret === undefined ? "Copy the client ID into your server's configuration. It signs in with its own private key." : "Copy the client ID and the secret into your server's configuration."}
      footer={
        <Button isDisabled={!confirmed} onPress={onDone}>
          Done
        </Button>
      }
    >
      <div className="space-y-4">
        {secret !== undefined ? (
          <Banner tone="warn" title="Shown only once">
            {SECRET_WARNING}
            {issued.old_credential_valid_until ? ` The credential you rotated keeps working until ${new Date(issued.old_credential_valid_until).toLocaleString("en-GB")}.` : ""}
          </Banner>
        ) : (
          <Banner tone="info" title="No secret to keep">
            {KEY_CREDENTIAL_NOTE}
            {issued.old_credential_valid_until ? ` The credential you rotated keeps working until ${new Date(issued.old_credential_valid_until).toLocaleString("en-GB")}.` : ""}
          </Banner>
        )}
        <CopyField label="Client ID" value={issued.client_id} />
        {secret !== undefined ? <CopyField label="Secret" value={secret} /> : issued.key_id ? <CopyField label="Public key ID" value={issued.key_id} /> : null}
        {secret !== undefined ? (
          <Checkbox isSelected={confirmed} onChange={setConfirmed}>
            I have copied the secret and stored it safely.
          </Checkbox>
        ) : null}
      </div>
    </Dialog>
  );
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-ink-muted">{label}</p>
      <div className="flex items-stretch gap-2">
        <input readOnly value={value} aria-label={label} onFocus={(e) => e.currentTarget.select()} className="min-w-0 flex-1 rounded-md border border-border bg-surface-sunken px-3 py-2 font-mono text-xs text-ink" />
        <Button
          variant="secondary"
          size="sm"
          icon={copied ? <Check aria-hidden className="size-4 text-ok-fg" /> : <Copy aria-hidden className="size-4" />}
          onPress={() => {
            void navigator.clipboard?.writeText(value).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            });
          }}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

/** A credential that holds only a PUBLIC key: the server signs a short assertion with its own private key, so there is no secret to leak. A key with private material is refused here, before it is sent. */
function KeyDialog({ applicationId, onClose, onIssued }: { applicationId: string; onClose: () => void; onIssued: (r: VerifyApiIssued) => void }) {
  const [text, setText] = useState("");
  const key = parsePublicKey(text);
  const create = useCellMutation((api) => api.verifyApi.issueCredential(applicationId, key.jwk ?? undefined), { onSuccess: onIssued });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Create a key credential"
      description="Register the public half of a key your server holds. We never see the private half."
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={key.jwk === null} onPress={() => create.mutate(undefined)}>
            Create credential
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Banner tone="info" title="How it works">
          {KEY_CREDENTIAL_NOTE}
        </Banner>
        <TextField
          label="Public key (JSON Web Key)"
          value={text}
          onChange={setText}
          multiline
          rows={6}
          placeholder={'{"kty":"OKP","crv":"Ed25519","x":"..."}'}
          description="An EC P-256 (ES256) or Ed25519 (EdDSA) public key. Never paste a private key: it is refused."
          errorMessage={key.problem ?? undefined}
        />
        {create.error ? <ApiErrorView error={create.error} title="The credential was not created" /> : null}
      </div>
    </Dialog>
  );
}
