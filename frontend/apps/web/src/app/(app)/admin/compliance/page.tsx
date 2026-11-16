import type { Metadata } from "next";

import { CompliancePage } from "@/features/admin/compliance/compliance-page";

export const metadata: Metadata = { title: "Compliance cases" };

export default function Page() {
  return <CompliancePage />;
}
