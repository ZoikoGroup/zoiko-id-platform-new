import type { Metadata } from "next";

import { IdentityProofingPage } from "@/features/account/idv/identity-proofing";

export const metadata: Metadata = { title: "Identity check" };

export default function Page() {
  return <IdentityProofingPage />;
}
