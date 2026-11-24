import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { joinDestination } from "@/features/enroll/join";

export const metadata: Metadata = { title: "Join" };

/** The link in an invitation email. It carries one token; this page turns it into the passkey setup address and moves the person on. Nothing is shown here. */
export default async function JoinPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  redirect(joinDestination(sp.t));
}
