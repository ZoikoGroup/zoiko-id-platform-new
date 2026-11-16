import type { Metadata } from "next";

import { OwnershipDetailRoute } from "@/features/admin/ownership/route";

export const metadata: Metadata = { title: "Ownership graph" };

export default async function Page({ params }: { params: Promise<{ graphId: string }> }) {
  const { graphId } = await params;
  return <OwnershipDetailRoute graphId={graphId} />;
}
