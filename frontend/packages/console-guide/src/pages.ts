/** The console's pages, for "where do I..." answers. Paths mirror the navigation in apps/web `components/console-shell.tsx`; a test in
 * the web app keeps them in step. The guide only ever links to these fixed paths (never to anything a question or a server supplies). */

export interface GuidePage {
  id: string;
  label: string;
  href: string;
  group: "Company" | "My account" | "Administration";
  /** Words a person might use for it. */
  keywords: string;
}

export const PAGES: readonly GuidePage[] = [
  { id: "overview", label: "Overview", href: "/overview", group: "Company", keywords: "overview dashboard home summary start" },
  { id: "alerts", label: "Alerts & tasks", href: "/alerts", group: "Company", keywords: "alerts tasks notifications inbox to-do" },
  { id: "onboarding", label: "Onboarding", href: "/admin/onboarding", group: "Company", keywords: "onboarding setup get started checklist activation pilot plan" },
  { id: "organization", label: "Company verification", href: "/admin/organization", group: "Company", keywords: "company verification organization kyb legal entity registry" },
  { id: "domains", label: "Domains", href: "/admin/domains", group: "Company", keywords: "domains domain dns ownership" },
  { id: "applications", label: "Applications", href: "/admin/applications", group: "Company", keywords: "applications apps clients oauth relying party" },
  { id: "integrations", label: "Integrations", href: "/admin/integrations", group: "Company", keywords: "integrations connectors directory" },
  { id: "team", label: "Team & invitations", href: "/admin/team", group: "Company", keywords: "team members invite invitation invitations roles people users add remove" },
  { id: "setup", label: "Organization setup", href: "/organizations/current/setup", group: "Company", keywords: "organization setup structure units" },
  { id: "approvals", label: "Approvals", href: "/organizations/current/approvals", group: "Company", keywords: "approvals approve requests pending vote role grant ownership transfer" },
  { id: "billing", label: "Billing", href: "/admin/billing", group: "Company", keywords: "billing invoices payment plan subscription usage" },
  { id: "contacts", label: "Email contacts", href: "/admin/contacts", group: "Company", keywords: "email contacts notification addresses" },
  { id: "settings", label: "Company settings", href: "/admin/settings", group: "Company", keywords: "company settings configuration preferences" },
  { id: "account", label: "My account", href: "/account", group: "My account", keywords: "my account profile name" },
  { id: "sessions", label: "Sessions", href: "/account/sessions", group: "My account", keywords: "sessions devices signed in sign out" },
  { id: "passkeys", label: "Passkeys", href: "/account/passkeys", group: "My account", keywords: "passkeys passkey authenticator security key sign in" },
  { id: "my-limits", label: "My limits", href: "/account/limits", group: "My account", keywords: "limits my limits" },
  { id: "connected-apps", label: "Connected apps", href: "/account/connected-apps", group: "My account", keywords: "connected apps consent authorised third party" },
  { id: "my-verification", label: "Identity verification", href: "/account/verification", group: "My account", keywords: "identity verification verify myself" },
  { id: "recovery-codes", label: "Recovery codes", href: "/account/recovery", group: "My account", keywords: "recovery codes backup account recovery" },
  { id: "my-privacy", label: "Privacy", href: "/account/privacy", group: "My account", keywords: "privacy my data consent rights preferences" },
  { id: "idp", label: "Identity providers", href: "/admin/identity-providers", group: "Administration", keywords: "identity providers sso federation saml oidc enterprise single sign-on" },
  { id: "verification", label: "Verification", href: "/admin/verification", group: "Administration", keywords: "verification verifications adapters profiles kyc" },
  { id: "credentials", label: "Credentials & trust", href: "/admin/credentials", group: "Administration", keywords: "credentials credential issue issuance verifiable trust issuer wallet" },
  { id: "authority", label: "Authority & approvals", href: "/admin/authority", group: "Administration", keywords: "authority delegation delegate grants" },
  { id: "mandates", label: "Mandates", href: "/admin/mandates", group: "Administration", keywords: "mandates agent spending limits" },
  { id: "policies", label: "Policies", href: "/admin/policies", group: "Administration", keywords: "policies policy workflows approval policy" },
  { id: "decisions", label: "Access decisions", href: "/admin/decisions", group: "Administration", keywords: "access decisions decision allow deny" },
  { id: "signals", label: "Security signals", href: "/admin/signals", group: "Administration", keywords: "security signals posture risk" },
  { id: "accounts", label: "People & principals", href: "/admin/accounts", group: "Administration", keywords: "people principals accounts users" },
  { id: "provisioning", label: "Provisioning", href: "/admin/provisioning", group: "Administration", keywords: "provisioning scim sync users directory" },
  { id: "evidence", label: "Evidence & audit", href: "/admin/evidence", group: "Administration", keywords: "evidence audit ledger logs trail export" },
  { id: "privacy", label: "Privacy & data rights", href: "/admin/privacy", group: "Administration", keywords: "privacy data rights requests erasure retention" },
  { id: "operator-access", label: "Operator access", href: "/admin/operator-access", group: "Administration", keywords: "operator access staff support lockbox" },
  { id: "residency", label: "Residency", href: "/admin/residency", group: "Administration", keywords: "residency data region location" },
  { id: "agents", label: "Agents & tools", href: "/admin/agents", group: "Administration", keywords: "agents tools machine automation" },
  { id: "workloads", label: "Workload identity", href: "/admin/workloads", group: "Administration", keywords: "workload identity services spiffe" },
  { id: "ownership", label: "Ownership chains", href: "/admin/ownership", group: "Administration", keywords: "ownership chains beneficial owners ubo shareholders" },
  { id: "trust-store", label: "Chip trust store", href: "/admin/trust-store", group: "Administration", keywords: "chip trust store passport csca country signing certificates master list nfc" },
  { id: "verification-spend", label: "Verification spend", href: "/admin/verification-spend", group: "Administration", keywords: "verification spend budget monthly limit supplier cost usage finance" },
  { id: "engine-readiness", label: "Face engine readiness", href: "/admin/engine-readiness", group: "Administration", keywords: "face engine readiness licence dpia evaluation biometric selfie liveness approval" },
  { id: "compliance", label: "Compliance cases", href: "/admin/compliance", group: "Administration", keywords: "compliance cases waiting review person queue" },
  { id: "source-of-funds", label: "Source of funds", href: "/admin/source-of-funds", group: "Administration", keywords: "source of funds wealth cases review" },
  { id: "workflows", label: "Verification workflows", href: "/admin/workflows", group: "Administration", keywords: "verification workflows studio checks approval simulate" },
  { id: "developers", label: "Developers", href: "/admin/developers", group: "Administration", keywords: "developers api webhooks keys events sdk" },
];

/** Link id (what a packet carries) for a page. The widget turns it into `/guide/out/<id>`, and a fixed-map route redirects. */
export const linkId = (pageId: string): string => `PAGE-${pageId.toUpperCase()}`;

/** The one place a link id becomes a path. Anything not in this map is refused (no open redirect, no caller-supplied destination). */
export const GUIDE_LINKS: Readonly<Record<string, { label: string; href: string }>> = Object.fromEntries(
  PAGES.map((p) => [linkId(p.id), { label: p.label, href: p.href }]),
);

export function pageByLinkId(id: string): GuidePage | undefined {
  return PAGES.find((p) => linkId(p.id) === id);
}

/** The destination for a link id, or undefined. An own-property check: ids such as "constructor" or "__proto__" must never resolve. */
export function guideHref(id: string): string | undefined {
  return Object.hasOwn(GUIDE_LINKS, id) ? GUIDE_LINKS[id]?.href : undefined;
}
