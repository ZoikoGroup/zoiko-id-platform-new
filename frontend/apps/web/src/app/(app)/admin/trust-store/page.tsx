import type { Metadata } from "next";

import { TrustStoreRoute } from "@/features/admin/trust-store/route";

export const metadata: Metadata = { title: "Chip trust store" };

export default function Page() {
  return <TrustStoreRoute />;
}
