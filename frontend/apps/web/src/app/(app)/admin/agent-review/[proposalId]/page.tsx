import type { Metadata } from "next";

import { ReviewProposalPage } from "@/features/admin/agents/readiness/review-page";

export const metadata: Metadata = { title: "Review suggested access change" };

export default async function Page({ params }: { params: Promise<{ proposalId: string }> }) {
  return <ReviewProposalPage proposalId={(await params).proposalId} />;
}
