"use client";

import { ApiError, type ProvisioningSourceCreated, type SourceIn } from "@zoiko/api-client";
import { Banner, Button, Dialog, RadioCards, RiskBadge, TextField } from "@zoiko/ui";
import { CheckCircle2, PackageX } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation } from "@/lib/cell";

import { ConnectionDetails } from "./connection-details";
import { parseJwks } from "./jwks";

export const SOURCES_KEY = ["admin", "provisioning", "sources"];

/** The cell's column widths: a longer value is refused, so the form says so before sending. */
export const NAME_MAX = 128;
export const CLIENT_MAX = 256;

type Mode = "jwks" | "client";

/** A plan without SCIM (or with its sources used up) is a 402; anything else reads through the shared error view. */
export function SourceError({ error }: { error: unknown }) {
  if (error instanceof ApiError && (error.code === "ENTITLEMENT_REQUIRED" || error.code === "ENTITLEMENT_HOLD")) {
    const hold = error.code === "ENTITLEMENT_HOLD";
    return (
      <Banner tone="warn" icon={PackageX} title={hold ? "Plan check unavailable" : "Not included in your plan"} live="polite">
        {hold
          ? "The platform cannot confirm your plan right now, so it did not complete this. Try again in a moment."
          : "Automated SCIM provisioning, or the number of sources it allows, is not part of your current plan. Ask an Owner to change plan under Billing, then try again."}
        {error.correlationId ? <span className="mt-1 block text-xs">Support reference: {error.correlationId}</span> : null}
      </Banner>
    );
  }
  return <ApiErrorView error={error} />;
}

export function CreateSourceDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Mode>("jwks");
  const [keys, setKeys] = useState("");
  const [clientId, setClientId] = useState("");
  const [approval, setApproval] = useState("");
  const [created, setCreated] = useState<ProvisioningSourceCreated | null>(null);

  const create = useCellMutation((api, body: SourceIn) => api.provisioning.createSource(body), {
    invalidate: [SOURCES_KEY],
    success: "Provisioning source registered",
    onSuccess: (r) => setCreated(r),
  });

  function close() {
    if (create.isPending) return;
    setName("");
    setMode("jwks");
    setKeys("");
    setClientId("");
    setApproval("");
    setCreated(null);
    create.reset();
    onClose();
  }

  const nameTooLong = name.trim().length > NAME_MAX;
  const clientTooLong = clientId.trim().length > CLIENT_MAX;
  const parsed = mode === "jwks" && keys.trim() ? parseJwks(keys) : null;
  const keysOk = mode === "jwks" ? parsed?.jwks !== undefined : clientId.trim().length > 0 && !clientTooLong;
  const valid = name.trim().length > 0 && !nameTooLong && keysOk;

  function submit() {
    const common = { display_name: name.trim(), approval_request_id: approval.trim() || null };
    if (mode === "jwks" && parsed?.jwks) create.mutate({ ...common, jwks: parsed.jwks });
    else if (mode === "client") create.mutate({ ...common, client_id: clientId.trim() });
  }

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title={created ? "Source registered" : "Register SCIM provisioning source"}
      description={created ? undefined : "Binds one OAuth client to a SCIM 2.0 connector for this tenant."}
      size="lg"
      footer={
        created ? (
          <Button onPress={close}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" isDisabled={create.isPending} onPress={close}>
              Cancel
            </Button>
            <Button
              pending={create.isPending}
              isDisabled={!valid}
              onPress={submit}
            >
              Register source
            </Button>
          </>
        )
      }
    >
      {created ? (
        <div className="space-y-4">
          <Banner tone="ok" icon={CheckCircle2} title={`${created.display_name} is active`} live="polite">
            Configure your identity provider&apos;s SCIM connector with these details.
          </Banner>
          <ConnectionDetails
            sourceId={created.source_id}
            clientId={created.client_id}
            scimBaseUrl={created.scim_base_url}
            tokenEndpoint={created.token_endpoint}
            clientCreated={created.client_created}
          />
        </div>
      ) : (
        <div className="space-y-4">
          <RiskBadge risk="R3" />
          <p className="text-sm text-ink-muted">
            A provisioning source can create, change and suspend every account it provisions. It signs in as one OAuth client
            (grant <code className="font-mono">client_credentials</code>, scope <code className="font-mono">scim:provision</code>, authenticated by{" "}
            <code className="font-mono">private_key_jwt</code>). A client can back only one source, even after that source is suspended.
          </p>
          <TextField
            label="Display name"
            isRequired
            value={name}
            onChange={setName}
            placeholder="Workday HR"
            errorMessage={nameTooLong ? `Use at most ${NAME_MAX} characters.` : undefined}
          />
          <RadioCards
            label="OAuth client"
            value={mode}
            onChange={(v) => setMode(v as Mode)}
            options={[
              {
                id: "jwks",
                label: "Create the connector's OAuth client (paste its public keys, JWKS)",
                description: "The cell creates the client and its service identity. Paste public keys only; there is no secret to copy and a private key is refused.",
              },
              { id: "client", label: "Use an existing OAuth client ID", description: "The client must already exist in this tenant, be active, and hold client_credentials with scim:provision." },
            ]}
          />
          {mode === "jwks" ? (
            <TextField
              label="Public keys (JWKS)"
              isRequired
              multiline
              rows={6}
              mono
              value={keys}
              onChange={setKeys}
              placeholder='{"keys": [{"kty": "EC", "crv": "P-256", "kid": "scim-1", "x": "...", "y": "..."}]}'
              description="A JWK Set with 1 to 5 public keys, each with a kid."
              errorMessage={parsed?.error}
            />
          ) : (
            <TextField
              label="OAuth client ID"
              isRequired
              value={clientId}
              onChange={setClientId}
              mono
              errorMessage={clientTooLong ? `Use at most ${CLIENT_MAX} characters.` : undefined}
            />
          )}
          <TextField
            label="Approval request ID (optional)"
            value={approval}
            onChange={setApproval}
            mono
            description="Needed when tenant policy requires a second administrator. The approval binds to create:<client ID>, or to create:jwks:<key ids, sorted, comma-separated> when you paste keys."
          />
          {create.error ? <SourceError error={create.error} /> : null}
        </div>
      )}
    </Dialog>
  );
}
