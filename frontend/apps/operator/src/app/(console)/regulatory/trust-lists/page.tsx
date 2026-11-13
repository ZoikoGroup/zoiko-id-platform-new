import type { Metadata } from "next";

import { TrustListsPage } from "@/features/regulatory/trust-lists";

export const metadata: Metadata = { title: "Trust lists" };

export default function Page() {
  return <TrustListsPage />;
}
