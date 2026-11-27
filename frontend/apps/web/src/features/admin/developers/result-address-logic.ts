import { shortEvent } from "./shared";

/** The identity verification events: the result of a check, and a check that ended without one. Matched on the short name, because the catalogue spells them com.zoiko.id.verification.api.session_decided.v1. */
export const isIdentityEvent = (type: string): boolean => shortEvent(type).startsWith("verification.api.");

/** What is wrong with an address, in words, before it is sent anywhere. Null when it looks right or nothing is typed yet. The platform checks all of this again. */
export function addressProblem(raw: string): string | null {
  const v = raw.trim();
  if (v === "") return null;
  if (/\s/.test(v)) return "The address has a space in it.";
  if (!/^https:\/\//i.test(v)) return "Start the address with https://";
  let host = "";
  let port = "";
  try {
    const u = new URL(v);
    host = u.hostname;
    port = u.port;
  } catch {
    return "That does not look like a web address.";
  }
  if (host === "localhost" || host.endsWith(".local")) return "localhost cannot be reached from the internet. While you test on your own machine, use a tunnel address (for example from cloudflared).";
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.includes(":")) return "Use a name, not an IP address.";
  if (port !== "") return "Use the standard port: leave the :port out.";
  if (!host.includes(".")) return "That does not look like a full web address.";
  return null;
}

/** The three things the address has to do, said once and plainly. */
export const ADDRESS_RULES: string[] = [
  "It is a public https:// address on your own server, with a name (not an IP address) and the standard port.",
  "When we test it, it answers straight away with 200 and the same challenge token we sent, as JSON: {\"challenge\": \"<the token>\"}. It does not redirect or ask anyone to sign in.",
  "After that, every result we send is signed. Check the signature, then answer 200. The Connect your project tab shows how.",
];
