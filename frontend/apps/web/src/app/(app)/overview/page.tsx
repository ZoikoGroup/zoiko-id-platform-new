import type { Metadata } from "next";

import { carried, carryQuery } from "@/features/onboarding/carry";
import { OverviewPage } from "@/features/overview/overview-page";

export const metadata: Metadata = { title: "Overview" };

export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  return <OverviewPage welcome={sp.welcome === "1"} carry={carryQuery(carried(sp))} />;
}
