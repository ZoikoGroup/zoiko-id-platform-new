import type { Metadata } from "next";

import { SetupPage } from "@/features/organizations/setup-page";

export const metadata: Metadata = { title: "Organization setup" };

export default async function Page({ params }: { params: Promise<{ orgId: string }> }) {
  const p = await params;
  return <SetupPage orgParam={p.orgId} />;
}
