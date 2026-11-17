import type { Metadata } from "next";

import { KybPolicyPage } from "@/features/regulatory/kyb-policy";

export const metadata: Metadata = { title: "Company verification packs" };

export default function Page() {
  return <KybPolicyPage />;
}
