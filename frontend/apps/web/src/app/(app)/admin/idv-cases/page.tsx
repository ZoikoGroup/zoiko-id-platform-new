import type { Metadata } from "next";

import { IdvCasesPage } from "@/features/admin/idv-cases/idv-cases";

export const metadata: Metadata = { title: "Identity proofing cases" };

export default function Page() {
  return <IdvCasesPage />;
}
