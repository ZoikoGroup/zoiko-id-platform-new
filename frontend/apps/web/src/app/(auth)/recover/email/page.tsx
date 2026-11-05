import type { Metadata } from "next";

import { cookies } from "next/headers";

import { RecoverEmail } from "@/features/recover/recover-email";
import { tokenFromQuery } from "@/features/sign-up/logic";
import { TICKET_COOKIE } from "@/server/registration";

export const metadata: Metadata = { title: "Sign back in", referrer: "no-referrer", robots: { index: false, follow: false } };

export default async function RecoverEmailPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const jar = await cookies();
  const ticket = jar.get(TICKET_COOKIE)?.value;
  return <RecoverEmail token={tokenFromQuery(sp.t)} ticket={ticket ? decodeURIComponent(ticket) : null} />;
}
