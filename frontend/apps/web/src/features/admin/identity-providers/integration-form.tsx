"use client";

import type { IntegrationIn } from "@zoiko/api-client";
import { Banner, Checkbox, FieldGroup, RadioCards, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { jsonInput, listInput, useCellMutation, useCellQuery } from "@/lib/cell";

import {
  AlgorithmPicker,
  FormDialog,
  certificatesInput,
  isoFromLocal,
  localDateTimeFromNow,
  optional,
  required,
  type Alg,
} from "./shared";

export const INTEGRATIONS_KEY = ["admin", "federation", "integrations"];
export const ANCHORS_KEY = ["admin", "federation", "trust-anchors"];

type Protocol = "OIDC" | "SAML";
type OidcMode = "static" | "federation";
type ClientAuth = "private_key_jwt" | "client_secret_basic";

/** Create an OIDC or SAML integration. Mirrors IntegrationIn and the profile CHECKs on
 * authn.federation_connection, so most mistakes are caught before the request. */
export function CreateIntegrationDialog({ onClose }: { onClose: () => void }) {
  const anchors = useCellQuery(ANCHORS_KEY, (api) => api.federation.trustAnchors());

  const [protocol, setProtocol] = useState<Protocol>("OIDC");
  const [displayName, setDisplayName] = useState("");
  const [issuer, setIssuer] = useState("");
  const [expires, setExpires] = useState(() => localDateTimeFromNow(180));
  const [linkPolicy, setLinkPolicy] = useState<"EXISTING_ONLY" | "JIT">("EXISTING_ONLY");
  const [trustAmr, setTrustAmr] = useState(true);
  const [acrLevels, setAcrLevels] = useState("");
  const [algorithms, setAlgorithms] = useState<Alg[]>(["ES256", "RS256"]);
  const [approvalId, setApprovalId] = useState("");
  // OIDC
  const [oidcMode, setOidcMode] = useState<OidcMode>("static");
  const [clientId, setClientId] = useState("");
  const [clientAuth, setClientAuth] = useState<ClientAuth>("private_key_jwt");
  const [secretRef, setSecretRef] = useState("");
  const [authzEndpoint, setAuthzEndpoint] = useState("");
  const [tokenEndpoint, setTokenEndpoint] = useState("");
  const [jwks, setJwks] = useState("");
  const [scopes, setScopes] = useState("openid, email, profile");
  const [anchorId, setAnchorId] = useState<string | null>(null);
  const [entityId, setEntityId] = useState("");
  // SAML
  const [ssoUrl, setSsoUrl] = useState("");
  const [certificates, setCertificates] = useState("");
  const [spEntityId, setSpEntityId] = useState("");
  const [encrypted, setEncrypted] = useState(false);
  const [decryptionKeyRef, setDecryptionKeyRef] = useState("");

  const [localError, setLocalError] = useState<unknown>(null);
  const create = useCellMutation((api, body: IntegrationIn) => api.federation.createIntegration(body), {
    invalidate: [INTEGRATIONS_KEY],
    success: (r) => `Integration “${r.display_name}” created`,
    onSuccess: onClose,
  });

  function build(): IntegrationIn {
    if (!algorithms.length) throw new Error("Choose at least one signing algorithm.");
    const levels = jsonInput(acrLevels, "ACR levels");
    if (levels && !Object.values(levels).every((v) => Number.isInteger(v))) throw new Error("ACR levels map each acr value to an integer level.");
    const base: IntegrationIn = {
      protocol,
      display_name: required(displayName, "Display name"),
      issuer: required(issuer, protocol === "OIDC" ? "Issuer" : "IdP entity ID"),
      metadata_expires_at: isoFromLocal(expires, "Metadata valid until"),
      link_policy: linkPolicy,
      trust_amr: trustAmr,
      acr_levels: levels as Record<string, number> | undefined,
      algorithms,
      allow_idp_initiated: false,
      require_encrypted_assertions: false,
      approval_request_id: optional(approvalId),
    };
    if (protocol === "OIDC") {
      const oidc: IntegrationIn = {
        ...base,
        client_id: required(clientId, "Client ID"),
        client_auth: clientAuth,
        client_secret_ref: clientAuth === "client_secret_basic" ? required(secretRef, "Client secret reference") : null,
        scopes: listInput(scopes),
      };
      if (oidcMode === "federation") {
        if (!anchorId) throw new Error("Choose the trust anchor that vouches for this provider.");
        const entity = required(entityId, "Federation entity ID");
        if (!entity.startsWith("https://")) throw new Error("Federation entity ID must be an https:// URL.");
        return { ...oidc, trust_anchor_id: anchorId, federation_entity_id: entity };
      }
      const authz = required(authzEndpoint, "Authorization endpoint");
      const token = required(tokenEndpoint, "Token endpoint");
      if (!authz.startsWith("https://") || !token.startsWith("https://")) throw new Error("Endpoints must be https:// URLs.");
      const keys = jsonInput(jwks, "JWKS");
      if (!keys || !Array.isArray(keys.keys) || !keys.keys.length) throw new Error('JWKS must be a JSON object with a non-empty "keys" array.');
      return { ...oidc, authorization_endpoint: authz, token_endpoint: token, jwks: keys };
    }
    const sso = required(ssoUrl, "SSO URL");
    if (!sso.startsWith("https://")) throw new Error("SSO URL must be an https:// URL.");
    const certs = certificatesInput(certificates);
    if (!certs.length) throw new Error("Add at least one IdP signing certificate.");
    return {
      ...base,
      sso_url: sso,
      idp_certificates: certs,
      sp_entity_id: required(spEntityId, "SP entity ID"),
      require_encrypted_assertions: encrypted,
      sp_decryption_key_ref: encrypted ? required(decryptionKeyRef, "SP decryption key reference") : optional(decryptionKeyRef),
    };
  }

  function submit() {
    setLocalError(null);
    create.reset();
    try {
      create.mutate(build());
    } catch (e) {
      setLocalError(e);
    }
  }

  const anchorOptions = (anchors.data?.items ?? [])
    .filter((a) => a.status === "ACTIVE")
    .map((a) => ({ id: a.anchor_id, label: a.entity_id, description: a.allowed_domains.join(", ") }));

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title="Add identity provider"
      description="Connect an external OIDC or SAML identity provider. Trust material is pinned; nothing is fetched at sign-in."
      submitLabel="Create integration"
      pending={create.isPending}
      error={localError ?? create.error}
      onSubmit={submit}
      risk="R3"
    >
      <RadioCards
        label="Protocol"
        columns={2}
        value={protocol}
        onChange={(v) => setProtocol(v as Protocol)}
        options={[
          { id: "OIDC", label: "OpenID Connect", description: "Authorization code flow with PKCE against the provider." },
          { id: "SAML", label: "SAML 2.0", description: "SP-initiated Web SSO with signed assertions." },
        ]}
      />

      <FieldGroup legend="Provider">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Display name" isRequired value={displayName} onChange={setDisplayName} description="Shown on the sign-in page. Up to 128 characters." />
          <TextField
            label={protocol === "OIDC" ? "Issuer" : "IdP entity ID"}
            isRequired
            mono
            value={issuer}
            onChange={setIssuer}
            placeholder={protocol === "OIDC" ? "https://login.example.com" : "https://idp.example.com/saml"}
            description="Unique per tenant."
          />
          <TextField
            label="Metadata valid until"
            type="datetime-local"
            isRequired
            value={expires}
            onChange={setExpires}
            description="In the future and at most 400 days ahead. Rotate before it lapses."
          />
          <Select
            label="Account linking"
            value={linkPolicy}
            onChange={(v) => setLinkPolicy(v as "EXISTING_ONLY" | "JIT")}
            options={[
              { id: "EXISTING_ONLY", label: "Existing accounts only", description: "Users link a pre-existing Zoiko iD account." },
              { id: "JIT", label: "Just-in-time", description: "Create an account on first sign-in." },
            ]}
          />
        </div>
      </FieldGroup>

      {protocol === "OIDC" ? (
        <FieldGroup legend="OpenID Connect">
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField label="Client ID" isRequired mono value={clientId} onChange={setClientId} />
            <Select
              label="Client authentication"
              value={clientAuth}
              onChange={(v) => setClientAuth(v as ClientAuth)}
              options={[
                { id: "private_key_jwt", label: "private_key_jwt", description: "Recommended. Signed client assertion." },
                { id: "client_secret_basic", label: "client_secret_basic", description: "Shared secret held in the secret store." },
              ]}
            />
            {clientAuth === "client_secret_basic" ? (
              <TextField
                label="Client secret reference"
                isRequired
                mono
                value={secretRef}
                onChange={setSecretRef}
                description="Name of the secret in the platform secret store, never the secret itself."
                className="sm:col-span-2"
              />
            ) : null}
            <TextField
              label="Scopes"
              mono
              value={scopes}
              onChange={setScopes}
              description="Comma separated; openid is always added."
              className="sm:col-span-2"
            />
          </div>
          <RadioCards
            label="How the provider's keys and endpoints are trusted"
            columns={2}
            value={oidcMode}
            onChange={(v) => setOidcMode(v as OidcMode)}
            options={[
              { id: "static", label: "Pinned configuration", description: "Enter endpoints and the provider's JWKS." },
              { id: "federation", label: "OpenID Federation", description: "Resolve the provider through a configured trust anchor." },
            ]}
          />
          {oidcMode === "static" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField label="Authorization endpoint" isRequired mono type="url" value={authzEndpoint} onChange={setAuthzEndpoint} placeholder="https://…/authorize" />
              <TextField label="Token endpoint" isRequired mono type="url" value={tokenEndpoint} onChange={setTokenEndpoint} placeholder="https://…/token" />
              <TextField
                label="JWKS"
                isRequired
                multiline
                rows={5}
                mono
                value={jwks}
                onChange={setJwks}
                placeholder='{"keys": [ … ]}'
                description="The provider's public signing keys, pinned."
                className="sm:col-span-2"
              />
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <Select
                label="Trust anchor"
                isRequired
                value={anchorId}
                onChange={setAnchorId}
                options={anchorOptions}
                placeholder={anchors.isPending ? "Loading…" : anchorOptions.length ? "Select…" : "No active trust anchors"}
                description="Add anchors on the Trust anchors tab."
              />
              <TextField label="Federation entity ID" isRequired mono type="url" value={entityId} onChange={setEntityId} placeholder="https://op.example.com" />
            </div>
          )}
        </FieldGroup>
      ) : (
        <FieldGroup legend="SAML 2.0">
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField label="SSO URL" isRequired mono type="url" value={ssoUrl} onChange={setSsoUrl} placeholder="https://idp.example.com/sso" />
            <TextField label="SP entity ID" isRequired mono value={spEntityId} onChange={setSpEntityId} description="How the IdP knows this tenant." />
            <TextField
              label="IdP signing certificates"
              isRequired
              multiline
              rows={5}
              mono
              value={certificates}
              onChange={setCertificates}
              placeholder="-----BEGIN CERTIFICATE-----"
              description="One or more PEM blocks, or one base64 DER certificate per line."
              className="sm:col-span-2"
            />
          </div>
          <Checkbox isSelected={encrypted} onChange={setEncrypted} description="Reject assertions that are not encrypted to this tenant's SP key.">
            Require encrypted assertions
          </Checkbox>
          <TextField
            label="SP decryption key reference"
            isRequired={encrypted}
            mono
            value={decryptionKeyRef}
            onChange={setDecryptionKeyRef}
            description="Key name in the platform key store. Required when assertions must be encrypted."
          />
          <Banner tone="info" title="IdP-initiated SSO is off">
            It can only be switched on afterwards, as a separate R3 change approved by a second administrator.
          </Banner>
        </FieldGroup>
      )}

      <FieldGroup legend="Token and assurance handling">
        <AlgorithmPicker legend="Accepted signing algorithms" value={algorithms} onChange={setAlgorithms} />
        <Checkbox isSelected={trustAmr} onChange={setTrustAmr} description="Use the provider's authentication methods (amr) as evidence for session assurance.">
          Trust the provider&apos;s amr claim
        </Checkbox>
        <TextField
          label="ACR levels (optional)"
          multiline
          rows={3}
          mono
          value={acrLevels}
          onChange={setAcrLevels}
          placeholder='{"urn:example:mfa": 2}'
          description="Maps the provider's acr values to an assurance level. Levels are capped at 2: federated sign-in never yields AAL-Z3."
        />
      </FieldGroup>

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
