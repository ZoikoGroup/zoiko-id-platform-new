import type { Metadata } from "next";

import { EmailChange } from "@/features/account/email-change";
import { tokenFromQuery } from "@/features/sign-up/logic";

export const metadata: Metadata = { title: "Confirm your new security email", referrer: "no-referrer", robots: { index: false, follow: false } };

export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  return <EmailChange token={tokenFromQuery(sp.t)} />;
}
