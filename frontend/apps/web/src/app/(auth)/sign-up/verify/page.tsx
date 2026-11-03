import type { Metadata } from "next";

import { cookies } from "next/headers";

import { tokenFromQuery } from "@/features/sign-up/logic";
import { Verify } from "@/features/sign-up/verify";
import { ADDRESS_COOKIE, TICKET_COOKIE } from "@/server/registration";

export const metadata: Metadata = { title: "Verify your email", referrer: "no-referrer", robots: { index: false, follow: false } };

export default async function VerifyPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const jar = await cookies();
  const ticket = jar.get(TICKET_COOKIE)?.value;
  const held = ticket ? { ticket: decodeURIComponent(ticket), address: decodeURIComponent(jar.get(ADDRESS_COOKIE)?.value ?? "") } : null;
  return <Verify token={tokenFromQuery(sp.t)} held={held} />;
}
