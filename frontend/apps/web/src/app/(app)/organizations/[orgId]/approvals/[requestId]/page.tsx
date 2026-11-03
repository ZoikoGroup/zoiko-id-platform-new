import type { Metadata } from "next";

import { DecisionPage } from "@/features/organizations/decision-page";

export const metadata: Metadata = { title: "Approval request" };

export default async function Page({ params }: { params: Promise<{ orgId: string; requestId: string }> }) {
  const p = await params;
  return <DecisionPage orgParam={p.orgId} requestId={p.requestId} />;
}
