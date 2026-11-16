import type { Metadata } from "next";

import { WorkflowRunsPage } from "@/features/admin/workflows/runs";

export const metadata: Metadata = { title: "Workflow runs" };

export default function Page() {
  return <WorkflowRunsPage />;
}
