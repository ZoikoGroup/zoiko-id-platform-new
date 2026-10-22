import type { Metadata } from "next";

import { AccountOverview } from "@/features/account/overview";

export const metadata: Metadata = { title: "My account" };

export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  return <AccountOverview welcome={sp.welcome === "1"} />;
}
