import type { Metadata } from "next";

import { EvidencePage } from "@/features/admin/evidence/evidence-page";

export const metadata: Metadata = { title: "Evidence & audit" };

export default function Page() {
  return <EvidencePage />;
}
