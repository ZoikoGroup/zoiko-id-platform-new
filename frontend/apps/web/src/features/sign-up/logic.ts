import type { RegistrationCapabilities, RegistrationNextStep, RegistrationStatus } from "@zoiko/api-client";

/**
 * Pure rules for person-first sign-up (ZID-UX-WF-013A). The server decides everything that matters (the country's home region, the
 * account, the verification status); this only validates what the form can know, and turns the server's codes into the plain words the
 * document asks for. Nothing here is an authorization decision.
 */

export const GENERIC_MESSAGE = "If this address can be used, check your inbox for next steps.";
export const EXPIRED_MESSAGE = "This link is no longer valid. Request a new link.";
export const HOLD_MESSAGE = "We couldn't complete registration securely. Contact support or try again later.";

export type Journey = "PERSONAL" | "ORGANIZATION";
export const JOURNEYS: { id: Journey; label: string; description: string }[] = [
  { id: "PERSONAL", label: "For myself", description: "A personal workspace. You can be invited to an organization later." },
  { id: "ORGANIZATION", label: "For an organization", description: "You will start a provisional claim after your account is secure. Nothing is verified by signing up." },
];

/** ISO 3166-1 alpha-2, the same set the server accepts. The person chooses one; none is ever preselected or inferred. */
export const COUNTRY_CODES = (
  "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI " +
  "CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR " +
  "GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU " +
  "LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL " +
  "PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO " +
  "TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW"
).split(" ");

/** The country's name in the display language (names only: the display language never chooses the country). */
export function countryName(code: string, locale = "en"): string {
  try {
    return new Intl.DisplayNames([locale], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

const fold = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

/** Countries matching what was typed, by name or code, ignoring case and accents; sorted by name. Empty query lists all. */
export function filterCountries(query: string, locale = "en"): { code: string; name: string }[] {
  const q = fold(query.trim());
  return COUNTRY_CODES.map((code) => ({ code, name: countryName(code, locale) }))
    .filter((c) => !q || fold(c.name).includes(q) || c.code.toLowerCase() === q)
    .sort((a, b) => a.name.localeCompare(b.name, locale));
}

// ------------------------------------------------------------------------------------------------ validation

export type FormValues = { journey: Journey | null; country: string; email: string };
export type FieldError = { field: "journey" | "country" | "email"; message: string };

/** A shape check only: one @, no spaces or control characters, a dotted domain. Unicode is allowed; the server decides what is valid. */
export function emailLooksValid(raw: string): boolean {
  const v = raw.trim();
  if (v.length < 3 || v.length > 254 || v.split("@").length !== 2) return false;
  if (/[\s\u0000-\u001f\u007f<>,;:"\\]/.test(v)) return false;
  const [local = "", domain = ""] = v.split("@");
  return local.length >= 1 && local.length <= 64 && /^[^.][^\s]*\.[^.\s]{2,}$/.test(domain) && !domain.includes("..");
}

export function validate(v: FormValues): FieldError[] {
  const out: FieldError[] = [];
  if (!v.journey) out.push({ field: "journey", message: "Choose For myself or For an organization." });
  if (!v.country || !COUNTRY_CODES.includes(v.country)) out.push({ field: "country", message: "Choose the country you live in." });
  if (!emailLooksValid(v.email)) out.push({ field: "email", message: "Enter a valid email address." });
  return out;
}

export type CountryState =
  | { kind: "none" }
  | { kind: "ok"; organizationClaims: boolean }
  | { kind: "other_region"; url: string | null }
  | { kind: "unavailable"; message: string; contact: string | null };

/** What the server said about the chosen country, in the states the document names. Unavailable and other-region refuse to submit. */
export function countryState(caps: RegistrationCapabilities | undefined, chosen: string): CountryState {
  const c = caps?.country;
  if (!chosen || !c || c.code !== chosen) return { kind: "none" };
  if (c.account === "AVAILABLE") return { kind: "ok", organizationClaims: c.organization_claims === "AVAILABLE" };
  if (c.account === "AVAILABLE_IN_ANOTHER_REGION") return { kind: "other_region", url: c.routing.url };
  return { kind: "unavailable", message: c.message ?? "Zoiko iD accounts are not available for this country yet.", contact: c.contact ?? null };
}

export const canSubmit = (s: CountryState) => s.kind === "ok";

/** The server's refusal codes in the document's words. An unknown code gets a neutral message, never the raw code. */
const SERVER_MESSAGES: Record<string, string> = {
  COUNTRY_INVALID: "Choose the country you live in.",
  EMAIL_INVALID: "Enter a valid email address.",
  INTENT_INVALID: "Choose For myself or For an organization.",
  NOTICE_VERSION_STALE: "The privacy notice was updated. Read the current notice and continue.",
  REGION_UNAVAILABLE: "Zoiko iD accounts are not available for this country yet. Nothing has been stored.",
  COUNTRY_CAPABILITY_UNAVAILABLE: "Account registration is not open for this country yet. Nothing has been stored.",
  CELL_ROUTING_REQUIRED: "Your account is held in a different region.",
  RATE_LIMITED: "Too many requests from this network. Try again in a few minutes.",
  REGISTRATION_DISABLED: "Sign-up is not available here right now.",
  LINK_INVALID: EXPIRED_MESSAGE,
};

export function serverMessage(code: string | undefined): string {
  return (code && SERVER_MESSAGES[code]) || "We couldn't complete that. Please try again.";
}

// -------------------------------------------------------------------------------------------- next steps

export type StepView = { title: string; body: string; action?: { label: string; href: string }; resend?: boolean; tone: "info" | "ok" | "warn" | "danger" };

/** What the person sees for each next step the server names. Account and identity are always shown separately by the caller. */
export function stepView(status: Pick<RegistrationStatus, "next_step" | "message">): StepView {
  const by: Record<RegistrationNextStep, StepView> = {
    VERIFY_EMAIL: { tone: "info", title: "Check your inbox", body: status.message ?? GENERIC_MESSAGE, resend: true },
    ENROLL_AUTHENTICATOR: {
      tone: "info",
      title: "Set up your passkey",
      body: "Your email is confirmed. Create a passkey to secure your account. If a passkey isn't available, a governed recovery path is used. We never accept email or SMS codes as a substitute.",
      action: { label: "Set up your passkey", href: "/sign-up/verify" },
    },
    PERSONAL_WORKSPACE: { tone: "ok", title: "Your account is ready", body: "Your personal workspace is open. Identity checks are asked for only when something you do needs them.", action: { label: "Open your workspace", href: "/account" } },
    ORGANIZATION_CLAIM: {
      tone: "ok",
      title: "Your account is ready",
      body: "Next, find your organization. This starts a provisional claim: nothing is verified and nobody becomes an Owner until the company checks are done.",
      action: { label: "Find your organization", href: "/admin/organization/claim" },
    },
    ACCEPT_INVITATION: { tone: "ok", title: "You have an invitation", body: "Sign in to see who invited you and to accept or decline.", action: { label: "Continue", href: "/account" } },
    REQUEST_NEW_LINK: { tone: "warn", title: "Request a new link", body: EXPIRED_MESSAGE, resend: true },
    CONTACT_SUPPORT: { tone: "danger", title: "Registration on hold", body: HOLD_MESSAGE, action: { label: "Get help", href: "/help" } },
  };
  return by[status.next_step] ?? by.VERIFY_EMAIL;
}

/** The two facts the document keeps apart: an active account is not an identity check. */
export function accountLabels(status: Pick<RegistrationStatus, "account" | "identity">): { account: string; identity: string } {
  return {
    account: status.account === "ACTIVE" ? "Account active" : "No account yet",
    identity: status.identity === "NOT_VERIFIED" ? "Identity not verified" : "Identity verified",
  };
}

// ----------------------------------------------------------------------------------------------- carrying

/** The one query value the verify page reads: the emailed link's own token. Anything else in the URL is ignored. */
export function tokenFromQuery(t: string | string[] | undefined): string | null {
  const v = Array.isArray(t) ? t[0] : t;
  return v && /^[A-Za-z0-9_-]{32,128}$/.test(v) ? v : null;
}
