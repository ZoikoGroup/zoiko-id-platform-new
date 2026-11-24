/** The person's side of a partner's verification link (CR-151). Pure helpers; the cell checks everything again. */

export type HostedView = {
  requested_by: string;
  purpose_code: string;
  locale: string;
  workflow: string;
  status: string;
  sandbox: boolean;
  link_expires_at: string | null;
  outcomes: string[];
  /** One of the approved themes; the page maps it to a fixed colour, never to anything the partner typed. */
  theme?: string;
  support_url?: string | null;
};

const TOKEN = /^zvl_[A-Za-z0-9_-]{20,80}$/;
export const validToken = (t: string): boolean => TOKEN.test(t);

export const PURPOSE: Record<string, string> = {
  customer_onboarding: "to open your account",
  fraud_prevention: "to protect against fraud",
  age_verification: "to confirm your age",
  account_recovery: "to recover your account",
  employment: "for an employment check",
};

export const purposeText = (code: string): string => PURPOSE[code] ?? "to confirm who you are";

export const OUTCOME: Record<string, { label: string; help: string }> = {
  approved: { label: "Pass", help: "The check succeeds." },
  declined: { label: "Fail", help: "The check does not succeed." },
  review: { label: "Needs a person to review", help: "A reviewer would look at it." },
  inconclusive: { label: "Could not be decided", help: "A provider problem, so no answer." },
  expired: { label: "Link runs out", help: "Nobody finished in time." },
};

/** What the person is told once the test has been answered. */
export const finishedText = (outcome: string): string =>
  outcome === "expired"
    ? "This test session ended without an answer. Ask the company for a new link."
    : "Your test answer was sent. You can close this page and go back to the company that asked.";

/** Where "Back to the company" may go: only an https address, which the cell already checked against the application's registered origins. */
export function safeReturn(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}

/** The one thing a wrong, used or expired link ever says. */
export const BAD_LINK =
  "This link cannot be used. It may have expired, been used, or been replaced. Ask the company that sent it for a new one.";

/** The approved themes and the fixed classes they map to. An unknown theme falls back to the standard colour, so nothing a partner sends can become a style. */
export const THEME_CLASS: Record<string, { bar: string; link: string }> = {
  blue: { bar: "bg-sky-600", link: "text-sky-700" },
  green: { bar: "bg-emerald-600", link: "text-emerald-700" },
  violet: { bar: "bg-violet-600", link: "text-violet-700" },
  slate: { bar: "bg-slate-600", link: "text-slate-700" },
};
export const themeClass = (theme: string | undefined): { bar: string; link: string } =>
  theme !== undefined && Object.hasOwn(THEME_CLASS, theme) ? THEME_CLASS[theme]! : THEME_CLASS.blue!;

/** The help link, only if it is a plain https address. */
export const safeSupport = (url: string | null | undefined): string | null => safeReturn(url);
