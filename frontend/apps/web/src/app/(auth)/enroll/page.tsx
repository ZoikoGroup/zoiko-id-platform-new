import type { Metadata } from "next";

import { Enroll } from "@/features/enroll/enroll";

export const metadata: Metadata = { title: "Set up your passkey" };

export default async function EnrollPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  return <Enroll ticket={sp.ticket} label={sp.label} company={sp.company} />;
}
