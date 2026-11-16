import type { Metadata } from "next";

import { WorkflowListPage } from "@/features/admin/workflows/workflows";

export const metadata: Metadata = { title: "Verification workflows" };

export default function Page() {
  return <WorkflowListPage />;
}
