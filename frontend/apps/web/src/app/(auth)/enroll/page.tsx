import type { Metadata } from "next";

import { Enroll } from "@/features/enroll/enroll";
import { carried, carryQuery } from "@/features/onboarding/carry";

export const metadata: Metadata = { title: "Set up your passkey" };

export default async function EnrollPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  return <Enroll ticket={sp.ticket} workspace={sp.workspace} selected={sp.selected === "1"} label={sp.label} company={sp.company} next={sp.next} carry={carryQuery(carried(sp))} />;
}
