"use client";

import type { IntegrationIn } from "@zoiko/api-client";
import { Banner, Button, Checkbox, RadioCards, Select, TextField } from "@zoiko/ui";
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

/** The providers most companies already use, with where to find each thing this form asks for. Choosing one sets the protocol,
 * suggests a name and tells the person (and their IT team) exactly what to collect. */
type ProviderKey = "ENTRA" | "OKTA" | "GOOGLE" | "OIDC" | "SAML";
const PROVIDERS: Record<
  ProviderKey,
  { label: string; description: string; protocol: Protocol; name: string; issuer: string; discovery?: (issuer: string) => string; steps: string[] }
> = {
  ENTRA: {
    label: "Microsoft Entra ID",
    description: "Formerly Azure Active Directory.",
    protocol: "OIDC",
    name: "Microsoft Entra ID",
    issuer: "https://login.microsoftonline.com/YOUR-DIRECTORY-ID/v2.0",
    discovery: (i) => `${i.replace(/\/$/, "")}/.well-known/openid-configuration`,
    steps: [
      "In the Entra admin center open App registrations, then New registration.",
      "Add the redirect address shown below as a Web redirect URI.",
      "Copy the Application (client) ID and your Directory (tenant) ID. The directory id goes into the issuer address.",
    ],
  },
  OKTA: {
    label: "Okta",
    description: "Your company's Okta sign-in.",
    protocol: "OIDC",
    name: "Okta",
    issuer: "https://YOUR-COMPANY.okta.com",
    discovery: (i) => `${i.replace(/\/$/, "")}/.well-known/openid-configuration`,
    steps: [
      "In the Okta admin console open Applications, then Create App Integration, then OIDC with Web Application.",
      "Add the redirect address shown below as a Sign-in redirect URI.",
      "Copy the Client ID. The issuer is your Okta address, for example https://yourcompany.okta.com.",
    ],
  },
  GOOGLE: {
    label: "Google Workspace",
    description: "Sign in with company Google accounts.",
    protocol: "OIDC",
    name: "Google Workspace",
    issuer: "https://accounts.google.com",
    discovery: () => "https://accounts.google.com/.well-known/openid-configuration",
    steps: [
      "In Google Cloud Console open APIs & Services, then Credentials, then Create OAuth client ID (Web application).",
      "Add the redirect address shown below as an Authorized redirect URI.",
      "Copy the Client ID. The issuer is https://accounts.google.com.",
    ],
  },
  OIDC: {
    label: "Another OpenID Connect provider",
    description: "Any provider that supports OpenID Connect.",
    protocol: "OIDC",
    name: "",
    issuer: "https://login.example.com",
    discovery: (i) => `${i.replace(/\/$/, "")}/.well-known/openid-configuration`,
    steps: [
      "Ask your IT team to register Zoiko iD as an application in the provider, using the redirect address shown below.",
      "They give you the Client ID and the provider's address (the issuer).",
    ],
  },
  SAML: {
    label: "A SAML 2.0 provider",
    description: "For example ADFS, Ping or Shibboleth.",
    protocol: "SAML",
    name: "",
    issuer: "https://idp.example.com/saml",
    steps: [
      "Ask your IT team for the provider's single sign-on address, its entity id and its signing certificate.",
      "After you save, this page shows the sign-in address (ACS URL) your IT team must enter on their side.",
    ],
  },
};

type OidcMode = "static" | "federation";
type ClientAuth = "private_key_jwt" | "client_secret_basic";

/** Create an OIDC or SAML integration. Mirrors IntegrationIn and the profile CHECKs on
 * authn.federation_connection, so most mistakes are caught before the request. */
export function CreateIntegrationDialog({ onClose }: { onClose: () => void }) {
  const anchors = useCellQuery(ANCHORS_KEY, (api) => api.federation.trustAnchors());

  const connection = useCellQuery(["onboarding", "connection"], (api) => api.onboarding.connection());
  const [provider, setProvider] = useState<ProviderKey>("ENTRA");
  const [protocol, setProtocol] = useState<Protocol>("OIDC");
  const [displayName, setDisplayName] = useState(PROVIDERS.ENTRA.name);
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

  const [metadataUrl, setMetadataUrl] = useState("");
  const [found, setFound] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const discover = useCellMutation((api, v: { protocol: Protocol; url: string }) => api.federation.discoverProvider(v.protocol, v.url), {
    onSuccess: (r) => {
      setDetailsOpen(false);
      if (r.protocol === "OIDC") {
        setIssuer(r.issuer);
        setAuthzEndpoint(r.authorization_endpoint);
        setTokenEndpoint(r.token_endpoint);
        setJwks(JSON.stringify(r.jwks));
        setFound(`Found the sign-in address, the token address and ${r.jwks.keys.length} signing key${r.jwks.keys.length === 1 ? "" : "s"}.`);
      } else {
        setIssuer(r.entity_id);
        setSsoUrl(r.sso_url);
        setCertificates(r.certificates.join("\n"));
        if (!spEntityId && connection.data) setSpEntityId(`${connection.data.api_base}/saml/sp`);
        setFound(`Found the sign-in address and ${r.certificates.length} signing certificate${r.certificates.length === 1 ? "" : "s"}.`);
      }
    },
  });
  const findIt = () => {
    setFound(null);
    discover.mutate(
      { protocol, url: protocol === "OIDC" ? issuer.trim() : metadataUrl.trim() },
      {
        onError: () => {
          setFound(null);
          setDetailsOpen(true); // fall back to filling the details by hand
        },
      },
    );
  };
  const canFind = protocol === "OIDC" ? issuer.trim().startsWith("https://") : metadataUrl.trim().startsWith("https://");

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title="Connect your company sign-in"
      description="Let people sign in with the account they already use at work. You need four things; we find the rest."
      submitLabel="Connect provider"
      pending={create.isPending}
      error={localError ?? create.error}
      onSubmit={submit}
      risk="R3"
    >
      <RadioCards
        label="Which sign-in does your company use today?"
        columns={2}
        value={provider}
        onChange={(v) => {
          const key = v as ProviderKey;
          setProvider(key);
          setProtocol(PROVIDERS[key].protocol);
          setFound(null);
          // a provider with one fixed address (Google) needs no typing; the others need the company's own
          if (!/YOUR|example/i.test(PROVIDERS[key].issuer)) setIssuer(PROVIDERS[key].issuer);
          setDisplayName((cur) => (!cur || Object.values(PROVIDERS).some((x) => x.name === cur) ? PROVIDERS[key].name : cur));
        }}
        options={(Object.keys(PROVIDERS) as ProviderKey[]).map((k) => ({ id: k, label: PROVIDERS[k].label, description: PROVIDERS[k].description }))}
      />

      <details className="rounded-card border border-border bg-sunken text-sm">
        <summary className="cursor-pointer px-4 py-3 font-medium text-ink">What to do at {PROVIDERS[provider].label} first</summary>
        <div className="space-y-2 border-t border-border p-4">
          <ol className="list-decimal space-y-1 pl-5 text-ink-muted">
            {PROVIDERS[provider].steps.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ol>
          {protocol === "OIDC" ? (
            <div className="pt-1">
              <p className="text-xs text-ink-subtle">The redirect address to give them:</p>
              <p className="select-all break-all rounded bg-surface px-2 py-1 font-mono text-xs text-ink">
                {connection.data ? `${connection.data.token_url.replace(/\/oauth2\/token$/, "")}/federation/callback` : "Loading…"}
              </p>
            </div>
          ) : null}
        </div>
      </details>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="Name people will see" isRequired value={displayName} onChange={setDisplayName} description="For example “Sign in with Okta”." />
        {protocol === "OIDC" ? (
          <TextField
            label="The provider's address (issuer)"
            isRequired
            mono
            value={issuer}
            onChange={(v) => {
              setIssuer(v);
              setFound(null);
            }}
            placeholder={PROVIDERS[provider].issuer}
            description="Your IT team can give you this."
          />
        ) : (
          <TextField
            label="The provider's metadata address"
            isRequired
            mono
            value={metadataUrl}
            onChange={(v) => {
              setMetadataUrl(v);
              setFound(null);
            }}
            placeholder="https://idp.example.com/metadata"
            description="Your IT team can give you this."
          />
        )}
        {protocol === "OIDC" ? (
          <TextField label="Client ID" isRequired mono value={clientId} onChange={setClientId} description="The provider gives you this after Zoiko iD is registered there." />
        ) : (
          <TextField
            label="Your entity id (we suggest one)"
            mono
            value={spEntityId}
            onChange={setSpEntityId}
            placeholder={connection.data ? `${connection.data.api_base}/saml/sp` : "https://…"}
            description="How the provider recognises Zoiko iD. Any address that is yours alone."
          />
        )}
      </div>

      <div className="space-y-2">
        <Button variant="secondary" pending={discover.isPending} isDisabled={!canFind} onPress={findIt}>
          Find the rest for me
        </Button>
        {found ? (
          <Banner tone="ok" title="Found it">
            {found} You can connect now.
          </Banner>
        ) : discover.isError ? (
          <Banner tone="warn" title="We could not find the details automatically">
            {discover.error instanceof Error ? discover.error.message : "Check the address."} You can fill the details in by hand below.
          </Banner>
        ) : (
          <p className="text-xs text-ink-subtle">We read the provider&apos;s public settings once, then pin them. Nothing is fetched when people sign in.</p>
        )}
      </div>

      <details className="rounded-card border border-border" open={detailsOpen} onToggle={(e) => setDetailsOpen((e.target as HTMLDetailsElement).open)}>
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-ink">
          Details <span className="font-normal text-ink-subtle">(filled in for you; open only to check or fix them)</span>
        </summary>
        <div className="space-y-4 border-t border-border p-4">
          {protocol === "OIDC" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField label="Sign-in address (authorization endpoint)" mono type="url" value={authzEndpoint} onChange={setAuthzEndpoint} placeholder="https://…/authorize" />
              <TextField label="Token address (token endpoint)" mono type="url" value={tokenEndpoint} onChange={setTokenEndpoint} placeholder="https://…/token" />
              <TextField
                label="Keys (the provider's public signing keys)"
                multiline
                rows={4}
                mono
                value={jwks}
                onChange={setJwks}
                placeholder='{"keys": [ … ]}'
                description="Public and safe to paste, from the provider's key list page."
                className="sm:col-span-2"
              />
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField label="The provider's entity id" mono value={issuer} onChange={setIssuer} placeholder="https://idp.example.com/saml" />
              <TextField label="Single sign-on address" mono type="url" value={ssoUrl} onChange={setSsoUrl} placeholder="https://idp.example.com/sso" />
              <TextField
                label="Signing certificates"
                multiline
                rows={4}
                mono
                value={certificates}
                onChange={setCertificates}
                placeholder="-----BEGIN CERTIFICATE-----"
                className="sm:col-span-2"
              />
            </div>
          )}
        </div>
      </details>

      <details className="rounded-card border border-border">
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-ink">
          Advanced settings <span className="font-normal text-ink-subtle">(the defaults are safe; most people skip this)</span>
        </summary>
        <div className="space-y-4 border-t border-border p-4">
          <Select
            label="Who may sign in with it?"
            value={linkPolicy}
            onChange={(v) => setLinkPolicy(v as "EXISTING_ONLY" | "JIT")}
            options={[
              { id: "EXISTING_ONLY", label: "Only people who already have an account", description: "The safer choice. They link their account the first time." },
              { id: "JIT", label: "Anyone from my company", description: "Their account is created the first time they sign in." },
            ]}
          />
          {protocol === "OIDC" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <Select
                label="How Zoiko iD proves itself to the provider"
                value={clientAuth}
                onChange={(v) => setClientAuth(v as ClientAuth)}
                options={[
                  { id: "private_key_jwt", label: "A signed key (recommended)", description: "No password is stored anywhere." },
                  { id: "client_secret_basic", label: "A shared secret", description: "The provider gave you a client secret, kept in the secret store." },
                ]}
              />
              <TextField label="Scopes" mono value={scopes} onChange={setScopes} description="Comma separated; openid is always added." />
              {clientAuth === "client_secret_basic" ? (
                <TextField
                  label="Name of the stored secret"
                  isRequired
                  mono
                  value={secretRef}
                  onChange={setSecretRef}
                  description="Name of the secret in the platform secret store, never the secret itself."
                  className="sm:col-span-2"
                />
              ) : null}
              <RadioCards
                label="How should we learn the provider's details?"
                columns={2}
                value={oidcMode}
                onChange={(v) => setOidcMode(v as OidcMode)}
                options={[
                  { id: "static", label: "Pinned settings (recommended)", description: "The addresses and keys above." },
                  { id: "federation", label: "Through a trust anchor (advanced)", description: "Only if your company runs OpenID Federation." },
                ]}
              />
              {oidcMode === "federation" ? (
                <>
                  <Select
                    label="Trust anchor"
                    value={anchorId}
                    onChange={setAnchorId}
                    options={anchorOptions}
                    placeholder={anchors.isPending ? "Loading…" : anchorOptions.length ? "Select…" : "No active trust anchors"}
                  />
                  <TextField label="Federation entity ID" mono type="url" value={entityId} onChange={setEntityId} placeholder="https://op.example.com" />
                </>
              ) : null}
            </div>
          ) : (
            <>
              <Checkbox isSelected={encrypted} onChange={setEncrypted} description="Reject assertions that are not encrypted to this tenant's key.">
                Require encrypted assertions
              </Checkbox>
              {encrypted ? <TextField label="Decryption key reference" isRequired mono value={decryptionKeyRef} onChange={setDecryptionKeyRef} /> : null}
            </>
          )}
          <TextField
            label="Valid until"
            type="datetime-local"
            isRequired
            value={expires}
            onChange={setExpires}
            description="How long these settings are trusted. Renew before it ends."
          />
          <AlgorithmPicker legend="Accepted signing algorithms" value={algorithms} onChange={setAlgorithms} />
          <Checkbox isSelected={trustAmr} onChange={setTrustAmr} description="Use the provider's authentication methods as evidence for session strength.">
            Trust the provider&apos;s authentication methods
          </Checkbox>
          <TextField label="Assurance levels (optional)" multiline rows={2} mono value={acrLevels} onChange={setAcrLevels} placeholder='{"urn:example:mfa": 2}' />
          <TextField label="Approval request ID (optional)" mono value={approvalId} onChange={setApprovalId} description="Only if your company requires a second administrator's approval for this change." />
        </div>
      </details>
    </FormDialog>
  );
}
