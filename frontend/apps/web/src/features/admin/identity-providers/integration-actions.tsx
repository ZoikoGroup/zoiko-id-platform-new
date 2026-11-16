"use client";

import type { FederationIntegration, IdpInitiatedIn, IntegrationUpdateIn, RotateIn } from "@zoiko/api-client";
import { Banner, Button, Checkbox, Code, CopyId, Dialog, Select, Switch, TextField } from "@zoiko/ui";
import { useState } from "react";

import { jsonInput, listInput, useCellMutation } from "@/lib/cell";

import { INTEGRATIONS_KEY } from "./integration-form";
import { parseProviderKeys } from "./provider-keys";
import { AlgorithmPicker, FormDialog, certificatesInput, isoFromLocal, localDateTimeFromNow, optional, required, type Alg } from "./shared";
import { UserText } from "@/lib/user-text";

type ClientAuth = "private_key_jwt" | "client_secret_basic";

/** Change what an integration is called and how it is used. Who is trusted (issuer, endpoints, keys, certificates) is not editable
 * here: rotate handles keys, and a different provider is a new integration. */
export function EditIntegrationDialog({ integration, onClose }: { integration: FederationIntegration; onClose: () => void }) {
  const saml = integration.protocol === "SAML";
  const [displayName, setDisplayName] = useState(integration.display_name);
  const [linkPolicy, setLinkPolicy] = useState<"EXISTING_ONLY" | "JIT">(integration.link_policy);
  const [trustAmr, setTrustAmr] = useState(integration.trust_amr);
  const [acrLevels, setAcrLevels] = useState(Object.keys(integration.acr_levels).length ? JSON.stringify(integration.acr_levels) : "");
  const [algorithms, setAlgorithms] = useState<Alg[]>(integration.algorithms as Alg[]);
  const [scopes, setScopes] = useState((integration.scopes ?? []).join(", "));
  const [spEntityId, setSpEntityId] = useState(integration.sp_entity_id ?? "");
  const [encrypted, setEncrypted] = useState(integration.require_encrypted_assertions);
  const [approvalId, setApprovalId] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);

  const update = useCellMutation((api, body: IntegrationUpdateIn) => api.federation.updateIntegration(integration.connection_id, body), {
    invalidate: [INTEGRATIONS_KEY],
    success: (r) => `${r.display_name} updated (revision ${r.config_revision})`,
    onSuccess: onClose,
  });

  function build(): IntegrationUpdateIn {
    if (!algorithms.length) throw new Error("Choose at least one signing algorithm.");
    const levels = jsonInput(acrLevels, "ACR levels") ?? {};
    if (!Object.values(levels).every((v) => Number.isInteger(v) && (v as number) >= 0))
      throw new Error("ACR levels map each acr value to a whole level, 0 or more (levels above 2 count as 2).");
    const body: IntegrationUpdateIn = {
      display_name: required(displayName, "Display name"),
      link_policy: linkPolicy,
      trust_amr: trustAmr,
      acr_levels: levels as Record<string, number>,
      algorithms,
      approval_request_id: optional(approvalId),
    };
    if (saml) {
      body.sp_entity_id = required(spEntityId, "SP entity ID");
      if (encrypted !== integration.require_encrypted_assertions) body.require_encrypted_assertions = encrypted;
    } else {
      body.scopes = listInput(scopes);
    }
    return body;
  }

  function submit() {
    setLocalError(null);
    update.reset();
    try {
      update.mutate(build());
    } catch (e) {
      setLocalError(e);
    }
  }

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title={`Edit ${integration.display_name}`}
      description="Name and usage settings. The provider's address, keys and certificates are not edited here: use Rotate for keys."
      submitLabel="Save changes"
      pending={update.isPending}
      error={localError ?? update.error}
      onSubmit={submit}
      risk="R3"
    >
      <TextField label="Name people will see" isRequired value={displayName} onChange={setDisplayName} />
      <Select
        label="Who may sign in with it?"
        value={linkPolicy}
        onChange={(v) => setLinkPolicy(v as "EXISTING_ONLY" | "JIT")}
        options={[
          { id: "EXISTING_ONLY", label: "Only people who already have an account" },
          { id: "JIT", label: "Anyone from my company", description: "Their account is created the first time they sign in." },
        ]}
      />
      {saml ? (
        <>
          <TextField label="Your entity id" isRequired mono value={spEntityId} onChange={setSpEntityId} description="The provider must be told if this changes." />
          <Checkbox
            isSelected={encrypted}
            onChange={setEncrypted}
            isDisabled={!integration.sp_decryption_key_ref && !integration.require_encrypted_assertions}
            description={
              integration.sp_decryption_key_ref
                ? "Reject assertions that are not encrypted to this tenant's key."
                : "Needs a decryption key reference, which is set when you rotate."
            }
          >
            Require encrypted assertions
          </Checkbox>
        </>
      ) : (
        <TextField label="Scopes" mono value={scopes} onChange={setScopes} description="Comma separated; openid is always added." />
      )}
      <AlgorithmPicker legend="Accepted signing algorithms" value={algorithms} onChange={setAlgorithms} />
      <Checkbox isSelected={trustAmr} onChange={setTrustAmr} description="Use the provider's authentication methods as evidence for session strength.">
        Trust the provider&apos;s authentication methods
      </Checkbox>
      <TextField label="Assurance levels (optional)" multiline rows={2} mono value={acrLevels} onChange={setAcrLevels} placeholder='{"urn:example:mfa": 2}' />
      <TextField label="Approval request ID (optional)" mono value={approvalId} onChange={setApprovalId} description="Only if your tenant policy requires a second administrator's approval for this change." />
    </FormDialog>
  );
}

/** New pinned trust material. The retired key or certificate stops verifying at once. */
export function RotateDialog({ integration, onClose }: { integration: FederationIntegration; onClose: () => void }) {
  const saml = integration.protocol === "SAML";
  const [expires, setExpires] = useState(() => localDateTimeFromNow(180));
  const [jwks, setJwks] = useState("");
  const [certificates, setCertificates] = useState("");
  const [clientAuth, setClientAuth] = useState<ClientAuth | null>(null);
  const [secretRef, setSecretRef] = useState("");
  const [decryptionKeyRef, setDecryptionKeyRef] = useState("");
  const [approvalId, setApprovalId] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);

  const rotate = useCellMutation((api, body: RotateIn) => api.federation.rotate(integration.connection_id, body), {
    invalidate: [INTEGRATIONS_KEY],
    success: (r) => `Trust material rotated (revision ${r.config_revision})`,
    onSuccess: onClose,
  });

  function build(): RotateIn {
    const body: RotateIn = {
      metadata_expires_at: isoFromLocal(expires, "Metadata valid until"),
      approval_request_id: optional(approvalId),
    };
    if (saml) {
      const certs = certificatesInput(certificates);
      if (certs.length) body.idp_certificates = certs;
      const keyRef = optional(decryptionKeyRef);
      if (keyRef) body.sp_decryption_key_ref = keyRef;
    } else {
      if (jwks.trim()) {
        const parsed = parseProviderKeys(jwks);
        if (parsed.error) throw new Error(parsed.error);
        body.jwks = parsed.keys;
      }
      if (clientAuth) body.client_auth = clientAuth;
      if (clientAuth === "client_secret_basic") body.client_secret_ref = required(secretRef, "Client secret reference");
      else if (optional(secretRef)) body.client_secret_ref = secretRef.trim();
    }
    return body;
  }

  function submit() {
    setLocalError(null);
    rotate.reset();
    try {
      rotate.mutate(build());
    } catch (e) {
      setLocalError(e);
    }
  }

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title={`Rotate ${integration.display_name}`}
      description="Replace pinned keys, certificates or secret references and extend metadata validity. Empty fields keep their current value."
      submitLabel="Rotate"
      pending={rotate.isPending}
      error={localError ?? rotate.error}
      onSubmit={submit}
      risk="R3"
    >
      <Banner tone="warn" title="Takes effect immediately">
        The old {saml ? "certificate" : "key set"} stops verifying as soon as this commits. Sign-ins already in flight with it will fail.
      </Banner>
      <TextField
        label="Metadata valid until"
        type="datetime-local"
        isRequired
        value={expires}
        onChange={setExpires}
        description="In the future and at most 400 days ahead."
      />
      {saml ? (
        <>
          <TextField
            label="New IdP signing certificates"
            multiline
            rows={5}
            mono
            value={certificates}
            onChange={setCertificates}
            placeholder="-----BEGIN CERTIFICATE-----"
            description="Replaces the whole certificate set. Leave empty to keep the current certificates."
          />
          <TextField
            label="SP decryption key reference"
            mono
            value={decryptionKeyRef}
            onChange={setDecryptionKeyRef}
            description="Leave empty to keep the current key."
          />
        </>
      ) : (
        <>
          <TextField
            label="New JWKS"
            multiline
            rows={5}
            mono
            value={jwks}
            onChange={setJwks}
            placeholder='{"keys": [ … ]}'
            description="Replaces the pinned key set. Leave empty to keep it (federation-resolved providers ignore it)."
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <Select
              label="Client authentication"
              value={clientAuth}
              onChange={(v) => setClientAuth(v as ClientAuth)}
              placeholder="Keep current"
              options={[
                { id: "private_key_jwt", label: "private_key_jwt" },
                { id: "client_secret_basic", label: "client_secret_basic" },
              ]}
            />
            <TextField
              label="Client secret reference"
              isRequired={clientAuth === "client_secret_basic"}
              mono
              value={secretRef}
              onChange={setSecretRef}
              description="Secret store name. Leave empty to keep the current one."
            />
          </div>
        </>
      )}
      <TextField
        label="Approval request ID (optional)"
        mono
        value={approvalId}
        onChange={setApprovalId}
        description="Only if your tenant policy requires a second administrator's approval for this change."
      />
    </FormDialog>
  );
}

/** IdP-initiated SAML. Switching on always needs an approval bound to exactly these targets. */
export function IdpInitiatedDialog({ integration, onClose }: { integration: FederationIntegration; onClose: () => void }) {
  const [enabled, setEnabled] = useState(!integration.allow_idp_initiated);
  const [targets, setTargets] = useState(integration.idp_initiated_targets.join("\n"));
  const [approvalId, setApprovalId] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);
  const [acsUrl, setAcsUrl] = useState<string | null>(null);

  const change = useCellMutation((api, body: IdpInitiatedIn) => api.federation.setIdpInitiated(integration.connection_id, body), {
    invalidate: [INTEGRATIONS_KEY],
    success: (r) => (r.allow_idp_initiated ? "IdP-initiated SSO switched on" : "IdP-initiated SSO switched off"),
    onSuccess: (r) => (r.acs_url ? setAcsUrl(r.acs_url) : onClose()),
  });

  const parsedTargets = listInput(targets);
  const sortedTargets = [...new Set(parsedTargets)].sort();

  function submit() {
    setLocalError(null);
    change.reset();
    try {
      if (enabled) {
        if (!sortedTargets.length) throw new Error("Add at least one landing target.");
        if (sortedTargets.length > 10) throw new Error("At most 10 landing targets.");
        for (const t of sortedTargets) {
          let u: URL;
          try {
            u = new URL(t);
          } catch {
            throw new Error(`“${t}” is not a URL.`);
          }
          if (u.protocol !== "https:" || u.hash || u.username) throw new Error(`“${t}” must be an exact https URL without a fragment.`);
        }
        change.mutate({ enabled: true, targets: sortedTargets, approval_request_id: required(approvalId, "Approval request ID") });
      } else {
        change.mutate({ enabled: false, targets: [], approval_request_id: optional(approvalId) });
      }
    } catch (e) {
      setLocalError(e);
    }
  }

  if (acsUrl) {
    return (
      <Dialog
        isOpen
        onOpenChange={(o) => !o && onClose()}
        title="IdP-initiated SSO is on"
        size="md"
        footer={<Button onPress={onClose}>Done</Button>}
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <p>Give the identity provider this connection-specific Assertion Consumer Service URL for unsolicited responses:</p>
          <div className="rounded-lg border border-border bg-canvas/60 p-3">
            <CopyId value={acsUrl} short={false} label="ACS URL" />
          </div>
        </div>
      </Dialog>
    );
  }

  const context = `tenant.identity_provider.change|federation_connection:${integration.connection_id}|idp_initiated:on:${sortedTargets.join(",")}`;

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title={`IdP-initiated SSO for ${integration.display_name}`}
      description="Unsolicited SAML responses are accepted only for this connection and only land on the exact targets listed."
      submitLabel={enabled ? "Switch on" : "Switch off"}
      pending={change.isPending}
      error={localError ?? change.error}
      onSubmit={submit}
      risk="R3"
    >
      <Switch isSelected={enabled} onChange={setEnabled}>
        Accept IdP-initiated sign-in
      </Switch>
      {enabled ? (
        <>
          <TextField
            label="Landing targets"
            isRequired
            multiline
            rows={4}
            mono
            value={targets}
            onChange={setTargets}
            placeholder="https://app.example.com/home"
            description="Exact https URLs, one per line (up to 10). No fragments."
          />
          <Banner tone="info" title="Needs a second administrator's approval">
            <p>
              Switching this on is an R3 change that always requires an approved approval request bound to exactly this change. Create one under{" "}
              <strong>Authority → Approvals</strong> with this transaction context:
            </p>
            <div className="mt-2 rounded-md border border-border bg-surface p-2">
              <span className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Transaction context</span>
              <CopyId value={context} short={false} label="transaction context" />
            </div>
            <p className="mt-2">Once it is approved, paste its ID below. Changing the targets afterwards needs a new approval.</p>
          </Banner>
          <TextField label="Approval request ID" isRequired mono value={approvalId} onChange={setApprovalId} />
        </>
      ) : (
        <p className="text-sm text-ink-muted">
          Switching off needs no approval. Unsolicited responses for this connection are refused from the moment it commits.
        </p>
      )}
    </FormDialog>
  );
}

/** The app's enterprise sign-in entry point for a connection. */
export function LoginUrlDialog({ integration, onClose }: { integration: FederationIntegration; onClose: () => void }) {
  const [url] = useState(() => `${window.location.origin}/auth/federation/${encodeURIComponent(integration.connection_id)}`);
  return (
    <Dialog isOpen onOpenChange={(o) => !o && onClose()} title="Sign-in link" size="md" footer={<Button onPress={onClose}>Done</Button>}>
      <div className="space-y-3 text-sm text-ink-muted">
        <p>
          Sends users to <strong className="text-ink"><UserText>{integration.display_name}</UserText></strong> and back into this app. Add{" "}
          <Code>?returnTo=/path</Code> to land somewhere other than the account page.
        </p>
        <div className="rounded-lg border border-border bg-canvas/60 p-3">
          <CopyId value={url} short={false} label="sign-in link" />
        </div>
        <Banner tone="info">
          The web app only starts sign-in for connections listed in its <Code>ZID_FEDERATION_CONNECTIONS</Code> setting; others are sent back
          to the sign-in page.
        </Banner>
      </div>
    </Dialog>
  );
}
