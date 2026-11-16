import type { Metadata } from "next";

import { WorkflowRunRoute } from "@/features/admin/workflows/runs";

export const metadata: Metadata = { title: "Workflow run" };

export default async function Page({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return <WorkflowRunRoute runId={runId} />;
}
