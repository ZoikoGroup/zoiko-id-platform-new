import type { Metadata } from "next";

import { InboxPage } from "@/features/organizations/inbox-page";

export const metadata: Metadata = { title: "Approvals" };

export default async function Page({ params }: { params: Promise<{ orgId: string }> }) {
  const p = await params;
  return <InboxPage orgParam={p.orgId} />;
}
