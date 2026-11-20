"use client";

import type { IntegrationIn } from "@zoiko/api-client";
import { Banner, Button, Checkbox, CopyId, Dialog, RadioCards, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { jsonInput, listInput, useCellMutation, useCellQuery } from "@/lib/cell";

import { parseProviderKeys, summarizeKeys } from "./provider-keys";
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

/** The keys found, laid out like the provider's own key list: one row per key, by its id. */
function KeyList({ keys }: { keys: { kid: string; kty: string; alg: string; use: string }[] }) {
  if (keys.length === 0) {
    return (
      <p className="rounded-card border border-dashed border-border px-4 py-3 text-sm text-ink-muted">
        No keys yet. Press <span className="font-medium text-ink">Find the rest for me</span> above and they appear here.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-card border border-border">
      <table className="w-full text-start text-sm" aria-label="Signing keys found">
        <thead className="bg-sunken text-xs uppercase tracking-wide text-ink-subtle">
          <tr>
            <th scope="col" className="px-3 py-2 text-start font-medium">Key ID</th>
            <th scope="col" className="px-3 py-2 text-start font-medium">Type</th>
            <th scope="col" className="px-3 py-2 text-start font-medium">Algorithm</th>
            <th scope="col" className="px-3 py-2 text-start font-medium">Use</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {keys.map((k, i) => (
            <tr key={`${k.kid}-${i}`}>
              <td className="px-3 py-2 font-mono text-xs text-ink">{k.kid}</td>
              <td className="px-3 py-2 text-ink-muted">{k.kty}</td>
              <td className="px-3 py-2 text-ink-muted">{k.alg || "any"}</td>
              <td className="px-3 py-2 text-ink-muted">{k.use || "any"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A numbered heading for one part of the form, so every group of fields is easy to find and none is hidden. */
function SectionTitle({ n, title, hint }: { n: number; title: string; hint?: string }) {
  return (
    <div className="flex items-start gap-3 border-t border-border pt-4 first:border-t-0 first:pt-0">
      <span aria-hidden className="mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-full bg-brand-500/15 text-xs font-semibold text-ink">
        {n}
      </span>
      <div>
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {hint ? <p className="text-xs text-ink-subtle">{hint}</p> : null}
      </div>
    </div>
  );
}

/** The providers most companies already use, with where to find each thing this form asks for. Choosing one sets the protocol,
 * suggests a name and tells the person (and their IT team) exactly what to collect. */
type ProviderKey = "ENTRA" | "OKTA" | "GOOGLE" | "OIDC" | "SAML";
const PROVIDERS: Record<
  ProviderKey,
  {
    label: string;
    description: string;
    protocol: Protocol;
    name: string;
    issuer: string;
    discovery?: (issuer: string) => string;
    steps: string[];
    /** How Zoiko iD proves itself when the provider cannot check a signed key (Google can only check a shared secret). */
    clientAuth?: ClientAuth;
  }
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
      "Copy the Client ID and the Client secret. The issuer is https://accounts.google.com.",
      "Google checks a shared secret, not a signed key: under Advanced settings choose \"A shared secret\" and give the name the secret is stored under.",
    ],
    clientAuth: "client_secret_basic",
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
  const [clientSecret, setClientSecret] = useState("");
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
  const [acsUrl, setAcsUrl] = useState<string | null>(null);
  const create = useCellMutation((api, body: IntegrationIn) => api.federation.createIntegration(body), {
    invalidate: [INTEGRATIONS_KEY],
    success: (r) => `Integration “${r.display_name}” created`,
    // a SAML provider's administrator must enter our sign-in address on their side: show it instead of closing
    onSuccess: (r) => (r.acs_url ? setAcsUrl(r.acs_url) : onClose()),
  });

  function build(): IntegrationIn {
    if (!algorithms.length) throw new Error("Choose at least one signing algorithm.");
    const levels = jsonInput(acrLevels, "ACR levels");
    if (levels && !Object.values(levels).every((v) => Number.isInteger(v) && (v as number) >= 0))
      throw new Error("ACR levels map each acr value to a whole level, 0 or more (levels above 2 count as 2).");
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
        // CR-138: the person enters the secret once; the platform seals it and keeps only a reference. A name in the operator's own secret store is still accepted.
        ...(clientAuth === "client_secret_basic"
          ? optional(clientSecret)
            ? { client_secret: clientSecret.trim() }
            : { client_secret_ref: required(secretRef, "Client secret (or the name of a stored secret)") }
          : {}),
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
      const parsed = parseProviderKeys(jwks);
      if (parsed.error) throw new Error(parsed.error);
      return { ...oidc, authorization_endpoint: authz, token_endpoint: token, jwks: parsed.keys };
    }
    const sso = required(ssoUrl, "SSO URL");
    if (!sso.startsWith("https://")) throw new Error("SSO URL must be an https:// URL.");
    const certs = certificatesInput(certificates);
    if (!certs.length) throw new Error("Add at least one IdP signing certificate.");
    return {
      ...base,
      sso_url: sso,
      idp_certificates: certs,
      sp_entity_id: required(spEntityId || suggestedSpEntityId, "SP entity ID"),
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

  const suggestedSpEntityId = connection.data ? `${connection.data.api_base}/saml/sp` : "";

  const anchorOptions = (anchors.data?.items ?? [])
    .filter((a) => a.status === "ACTIVE")
    .map((a) => ({ id: a.anchor_id, label: a.entity_id, description: a.allowed_domains.join(", ") }));

  const [metadataUrl, setMetadataUrl] = useState("");
  const [found, setFound] = useState<string | null>(null);
  const discover = useCellMutation((api, v: { protocol: Protocol; url: string }) => api.federation.discoverProvider(v.protocol, v.url), {
    onSuccess: (r) => {
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
          // the details are always visible below: fill them in by hand
        },
      },
    );
  };
  const canFind = protocol === "OIDC" ? issuer.trim().startsWith("https://") : metadataUrl.trim().startsWith("https://");

  if (acsUrl) {
    return (
      <Dialog isOpen onOpenChange={(o) => !o && onClose()} title="Provider connected" size="md" footer={<Button onPress={onClose}>Done</Button>}>
        <div className="space-y-3 text-sm text-ink-muted">
          <p>Give your IT team this sign-in address (Assertion Consumer Service URL) and your entity id to enter at the provider:</p>
          <div className="space-y-2 rounded-lg border border-border bg-canvas/60 p-3">
            <CopyId value={acsUrl} short={false} label="ACS URL" />
            <CopyId value={spEntityId || suggestedSpEntityId} short={false} label="SP entity ID" />
          </div>
        </div>
      </Dialog>
    );
  }

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title="Connect your company sign-in"
      description="Let people sign in with the account they already use at work. You need four things; we find the rest."
      submitLabel="Connect provider"
      size="xl"
      pending={create.isPending}
      error={localError ?? create.error}
      onSubmit={submit}
      risk="R3"
    >
      <SectionTitle n={1} title="Your sign-in provider" hint="Pick the one your company uses today." />
      <RadioCards
        label="Which sign-in does your company use today?"
        columns={2}
        value={provider}
        onChange={(v) => {
          const key = v as ProviderKey;
          setProvider(key);
          setProtocol(PROVIDERS[key].protocol);
          setClientAuth(PROVIDERS[key].clientAuth ?? "private_key_jwt");
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

      <SectionTitle n={2} title="What you need from your provider" hint="Four things. Fill these in, then press Find the rest for me." />
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

      <SectionTitle n={3} title="Connection details" hint="Filled in for you when you press Find the rest for me. You can also type them in or correct them here." />
      <section aria-label="Connection details" className="rounded-card border border-border bg-sunken/40">
        <div className="space-y-4 p-4">
          {protocol === "OIDC" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField label="Sign-in address (authorization endpoint)" mono type="url" value={authzEndpoint} onChange={setAuthzEndpoint} placeholder="https://…/authorize" />
              <TextField label="Token address (token endpoint)" mono type="url" value={tokenEndpoint} onChange={setTokenEndpoint} placeholder="https://…/token" />
              <div className="space-y-2 sm:col-span-2">
                <p className="text-sm font-medium text-ink">Signing keys</p>
                <p className="text-xs text-ink-subtle">The provider&apos;s public keys, which prove its sign-ins are genuine. Press Find the rest for me and we read them for you.</p>
                <KeyList keys={summarizeKeys(jwks)} />
                <details className="rounded-card border border-border">
                  <summary className="cursor-pointer px-4 py-2 text-sm text-ink-muted">Add or replace the keys by hand (advanced)</summary>
                  <div className="border-t border-border p-4">
                    <TextField
                      label="Public keys"
                      multiline
                      rows={4}
                      mono
                      value={jwks}
                      onChange={setJwks}
                      placeholder='{"keys": [ … ]}'
                      description="Only if the automatic read did not find them. Paste the provider's key list (its JWKS), public keys only. A list of key ids is not enough. Never paste a private key."
                    />
                  </div>
                </details>
              </div>
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
      </section>

      <SectionTitle n={4} title="Advanced settings" hint="Optional. The defaults are safe; most people skip this." />
      <details className="rounded-card border border-border bg-sunken/40">
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-ink">Show the advanced settings</summary>
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
                  { id: "client_secret_basic", label: "A shared secret", description: "The provider gave you a client secret. You enter it here once; it is sealed and never shown again." },
                ]}
              />
              <TextField label="Scopes" mono value={scopes} onChange={setScopes} description="Comma separated; openid is always added." />
              {clientAuth === "client_secret_basic" ? (
                <>
                  <TextField
                    label="Client secret"
                    type="password"
                    mono
                    autoComplete="off"
                    value={clientSecret}
                    onChange={setClientSecret}
                    description="From your provider's application settings. It is sealed as soon as you connect and never shown again. You can replace it later."
                    className="sm:col-span-2"
                  />
                  <TextField
                    label="Or the name of a stored secret (optional)"
                    mono
                    value={secretRef}
                    onChange={setSecretRef}
                    isDisabled={Boolean(clientSecret.trim())}
                    description="Only if your operator keeps the secret in their own secret store. Leave empty when you type the secret above."
                    className="sm:col-span-2"
                  />
                </>
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
