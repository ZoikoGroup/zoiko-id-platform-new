import type { Metadata } from "next";

import { Consent } from "@/features/consent/consent";

export const metadata: Metadata = { title: "Allow access" };

/** The cell redirects here as ZID_CONSENT_URL?consent_request=<tenant>.<request>. */
export default async function ConsentPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  return <Consent consentRef={sp.consent_request} />;
}
