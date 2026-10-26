"use client";

import type { OAuthClientCreate, OAuthClientRecord, OAuthClientType } from "@zoiko/api-client";
import { Banner, Button, Card, Checkbox, DataTable, Dialog, EmptyState, RadioCards, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { KeyRound, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

export const CLIENTS_KEY = ["integration", "oauth-clients"];

const SCOPES: { id: string; label: string; hint: string }[] = [
  { id: "openid", label: "Sign people in", hint: "Always included." },
  { id: "verification:read", label: "Read verification results", hint: "See whether a person is verified." },
  { id: "verification:execute", label: "Start verifications", hint: "Ask Zoiko iD to verify a person." },
  { id: "presentation:request", label: "Request credentials", hint: "Ask a person to present a credential." },
];

/** One URL per line in the form's redirect box. */
export function lines(v: string): string[] {
  return v
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}

const NOT_A_KEY = 'That is not a JSON key. Paste a JWK Set that looks like {"keys": [ … ]}.';

/** A pasted JWK Set, or a single JWK (wrapped), or the reason it cannot be read. */
export function parseKeys(v: string): { keys: Record<string, unknown>[] } | string {
  try {
    const j = JSON.parse(v) as unknown;
    if (j && typeof j === "object" && Array.isArray((j as { keys?: unknown }).keys)) return j as { keys: Record<string, unknown>[] };
    if (j && typeof j === "object" && "kty" in j) return { keys: [j as Record<string, unknown>] };
  } catch {
    /* fall through to the message */
  }
  return NOT_A_KEY;
}

function CreateClient({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [type, setType] = useState<OAuthClientType>("PUBLIC");
  const [redirects, setRedirects] = useState("");
  const [scopes, setScopes] = useState<string[]>(["openid"]);
  const [keys, setKeys] = useState("");
  const [keyError, setKeyError] = useState<string | undefined>();
  const [created, setCreated] = useState<OAuthClientRecord | null>(null);
  const create = useCellMutation((api, body: OAuthClientCreate) => api.integration.createOauthClient(body), {
    invalidate: [CLIENTS_KEY],
    success: "OAuth client created",
    onSuccess: (c) => setCreated(c),
  });

  function submit() {
    setKeyError(undefined);
    let jwks: OAuthClientCreate["jwks"] = null;
    if (type === "CONFIDENTIAL") {
      const parsed = parseKeys(keys);
      if (typeof parsed === "string") return setKeyError(parsed);
      jwks = parsed;
    }
    create.mutate({ name: name.trim(), client_type: type, redirect_uris: lines(redirects), scopes, jwks });
  }
  const valid = name.trim().length >= 2 && lines(redirects).length > 0 && (type === "PUBLIC" || keys.trim().length > 0);

  if (created) {
    return (
      <Dialog
        isOpen
        onOpenChange={(o) => !o && onClose()}
        title="Your client ID"
        description="Give this to your developers. There is no secret to keep."
        footer={<Button onPress={onClose}>Done</Button>}
      >
        <div className="space-y-4">
          <p className="select-all rounded-card border border-border bg-surface-muted p-3 font-mono text-sm text-ink" data-testid="new-client-id">
            {created.client_id}
          </p>
          <Banner tone="info" title="Why there is no secret">
            {created.client_type === "PUBLIC"
              ? "Browser and mobile apps sign in with PKCE, which proves each sign-in without a stored secret."
              : "Your server proves itself by signing a short-lived token with the private key you kept. Zoiko iD only holds the public half."}
          </Banner>
          <p className="text-sm text-ink-muted">
            To enforce access decisions, use this ID as the <strong>enforcement point client ID</strong> when you register the application.
          </p>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Create an OAuth client"
      description="The sign-in identity of one of your applications."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!valid} onPress={submit}>
            Create client
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField
          label="Application name"
          isRequired
          value={name}
          onChange={setName}
          placeholder="Payroll web app"
          description="Becomes the start of the client ID, so it is easy to recognise."
        />
        <RadioCards
          label="What kind of application is it?"
          value={type}
          onChange={(v) => setType(v as OAuthClientType)}
          options={[
            { id: "PUBLIC", label: "Browser or mobile app", description: "Runs on the person's device. Signs in with PKCE." },
            { id: "CONFIDENTIAL", label: "Server application", description: "Runs on your servers and holds a private key." },
          ]}
        />
        <TextField
          label="Redirect URLs"
          isRequired
          multiline
          rows={3}
          mono
          value={redirects}
          onChange={setRedirects}
          placeholder="https://app.example.com/callback"
          description="One per line. Exact https URLs only (plain http only for 127.0.0.1). People are sent back here after signing in."
        />
        {type === "CONFIDENTIAL" ? (
          <TextField
            label="Public key (JWK Set)"
            isRequired
            multiline
            rows={5}
            mono
            value={keys}
            onChange={setKeys}
            errorMessage={keyError}
            placeholder='{"keys": [{"kty": "EC", "crv": "P-256", "kid": "key-1", "x": "…", "y": "…"}]}'
            description="The public half only. Never paste a private key: it will be refused."
          />
        ) : null}
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-ink">What may it ask for?</legend>
          {SCOPES.map((s) => (
            <Checkbox
              key={s.id}
              isDisabled={s.id === "openid"}
              isSelected={scopes.includes(s.id)}
              onChange={() => setScopes((l) => (l.includes(s.id) ? l.filter((x) => x !== s.id) : [...l, s.id]))}
            >
              {s.label} <span className="text-xs text-ink-subtle">{s.hint}</span>
            </Checkbox>
          ))}
        </fieldset>
        {create.error ? <ApiErrorView error={create.error} title="The client was not created" /> : null}
      </div>
    </Dialog>
  );
}

/** The company's OAuth clients, with the id a developer needs to hand to an application (no secret exists). */
export function OAuthClientsCard() {
  const clients = useCellQuery(CLIENTS_KEY, (api) => api.integration.oauthClients());
  const [adding, setAdding] = useState(false);
  const retire = useCellMutation((api, id: string) => api.integration.retireOauthClient(id), {
    invalidate: [CLIENTS_KEY],
    success: "Client retired",
  });

  const columns: Column<OAuthClientRecord>[] = [
    { key: "id", header: "Client ID", pinned: true, render: (c) => <span className="select-all font-mono text-xs">{c.client_id}</span>, sortValue: (c) => c.client_id },
    { key: "type", header: "Kind", render: (c) => (c.client_type === "PUBLIC" ? "Browser or mobile" : "Server") },
    { key: "redirects", header: "Redirect URLs", render: (c) => <span className="text-xs">{c.redirect_uris.join(", ")}</span> },
    { key: "status", header: "Status", render: (c) => <StatusBadge state={c.status} kind="Application" size="xs" /> },
    {
      key: "actions",
      header: "",
      align: "end",
      render: (c) =>
        c.first_party || c.status === "RETIRED" ? null : (
          <Button size="sm" variant="secondary" pending={retire.isPending && retire.variables === c.client_id} onPress={() => retire.mutate(c.client_id)}>
            Retire
          </Button>
        ),
    },
  ];

  return (
    <Card
      padded={false}
      title="OAuth clients"
      description="Each application that signs people in has a client. The client ID identifies it; there is no secret to copy."
      actions={
        <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
          Create client
        </Button>
      }
    >
      {retire.error ? <ApiErrorView error={retire.error} title="The client was not retired" /> : null}
      <QueryView query={clients}>
        {(r) =>
          r.items.length ? (
            <DataTable caption="OAuth clients" columns={columns} rows={r.items} rowKey={(c) => c.client_id} />
          ) : (
            <EmptyState title="No OAuth clients yet" icon={KeyRound}>
              Create one for each application, then use its client ID when you register the application below.
            </EmptyState>
          )
        }
      </QueryView>
      {adding ? <CreateClient onClose={() => setAdding(false)} /> : null}
    </Card>
  );
}
