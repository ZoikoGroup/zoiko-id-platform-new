import type { Metadata } from "next";

import { TrustOperationsPage } from "@/features/trust-center/operations";

export const metadata: Metadata = { title: "Trust Center operations" };

export default function Page() {
  return <TrustOperationsPage />;
}
