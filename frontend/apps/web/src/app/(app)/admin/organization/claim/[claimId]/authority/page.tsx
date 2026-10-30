import type { Metadata } from "next";

import { AuthorityPage } from "@/features/onboarding/org-claim-page";

export const metadata: Metadata = { title: "Authority and owners" };

export default async function Page({ params }: { params: Promise<{ claimId: string }> }) {
  return <AuthorityPage claimId={(await params).claimId} />;
}
