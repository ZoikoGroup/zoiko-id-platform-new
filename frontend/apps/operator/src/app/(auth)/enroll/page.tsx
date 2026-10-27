import type { Metadata } from "next";

import { OperatorEnroll } from "@/features/enroll";

export const metadata: Metadata = { title: "Set up your passkey" };

export default async function EnrollPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  return <OperatorEnroll ticket={sp.ticket} />;
}
