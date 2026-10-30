import type { Metadata } from "next";

import { VerificationStatusPage } from "@/features/onboarding/org-claim-page";

export const metadata: Metadata = { title: "Verification status" };

export default async function Page({ params }: { params: Promise<{ claimId: string }> }) {
  return <VerificationStatusPage claimId={(await params).claimId} />;
}
