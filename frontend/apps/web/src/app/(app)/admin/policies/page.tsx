import type { Metadata } from "next";

import { PoliciesPage } from "@/features/admin/policies/policies-page";

export const metadata: Metadata = { title: "Policies" };

export default function Page() {
  return <PoliciesPage />;
}
