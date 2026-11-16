import type { Metadata } from "next";

import { OwnershipDetailPage } from "@/features/admin/ownership/ownership";

export const metadata: Metadata = { title: "Ownership graph" };

export default async function Page({ params }: { params: Promise<{ graphId: string }> }) {
  const { graphId } = await params;
  return <OwnershipDetailPage graphId={graphId} />;
}
