import type { Metadata } from "next";

import { TrustClaimsPage } from "@/features/trust-center/claims";

export const metadata: Metadata = { title: "Public claims" };

export default function Page() {
  return <TrustClaimsPage />;
}
