import type { Metadata } from "next";

import { VerificationSpendPage } from "@/features/admin/verification-spend/spend";

export const metadata: Metadata = { title: "Verification spend" };

export default function Page() {
  return <VerificationSpendPage />;
}
