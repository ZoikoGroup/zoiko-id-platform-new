import type { Metadata } from "next";

import { NewWorkflowPage } from "@/features/admin/workflows/workflows";

export const metadata: Metadata = { title: "New workflow" };

export default function Page() {
  return <NewWorkflowPage />;
}
