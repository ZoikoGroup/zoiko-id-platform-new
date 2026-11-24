import type { InvitationEmailResult } from "@zoiko/api-client";

/** What the inviter reads after asking for the invitation to be emailed: one plain sentence per outcome, and what to do next. Never a raw code. */
export type EmailNotice = { tone: "ok" | "warn" | "danger"; title: string; body: string };

export function emailNotice(result: InvitationEmailResult | undefined): EmailNotice | null {
  if (!result) return null;
  const to = result.to ? ` to ${result.to}` : "";
  switch (result.status) {
    case "SENT":
      return { tone: "ok", title: "Invitation emailed", body: `Sent${to}. The email carries the same kind of link: it works once and expires with this one.` };
    case "DUPLICATE":
      return { tone: "ok", title: "Invitation already emailed", body: `That email was already sent${to}.` };
    case "NOT_CONFIGURED":
    case "DISABLED":
      return { tone: "warn", title: "Email is not switched on", body: "Nothing was sent. Copy the link and share it yourself, or ask your administrator to switch on email." };
    case "INVALID_ADDRESS":
      return { tone: "danger", title: "That email address cannot be used", body: "Type one plain address, like name@company.com. Nothing was sent." };
    case "THROTTLED":
      return { tone: "warn", title: "Too many emails to this address", body: "Nothing was sent now. Try again in an hour, or share the link yourself." };
    case "SUPPRESSED":
      return { tone: "warn", title: "This address does not receive our emails", body: "Nothing was sent. Share the link with the person another way." };
    default:
      return { tone: "warn", title: "The email could not be sent right now", body: "The invitation and its link are fine. Try Send by email again, or share the link yourself." };
  }
}

/** A quick shape check so an obvious slip is caught before it is sent; the server checks again and decides. */
export function emailProblem(raw: string): string | null {
  const value = raw.trim();
  if (!value) return "Type the person's email address.";
  if (value.length > 254) return "That address is too long.";
  if (/[\s,;<>]/.test(value)) return "Type one plain address, like name@company.com.";
  if (!/^[^@]+@[^@.]+(\.[^@.]+)+$/.test(value)) return "That does not look like an email address.";
  return null;
}
