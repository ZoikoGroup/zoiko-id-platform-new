import type { Metadata } from "next";

import { WorkflowVersionRoute } from "@/features/admin/workflows/version-route";

export const metadata: Metadata = { title: "Workflow version" };

export default async function Page({ params }: { params: Promise<{ workflowId: string; version: string }> }) {
  const { workflowId, version } = await params;
  return <WorkflowVersionRoute id={workflowId} version={version} />;
}
