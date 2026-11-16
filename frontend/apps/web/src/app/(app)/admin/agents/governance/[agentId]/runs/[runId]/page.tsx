import type { Metadata } from "next";

import { RunDetailPage } from "@/features/admin/agents/governance/run-detail";

export const metadata: Metadata = { title: "Access readiness run" };

export default async function Page({ params }: { params: Promise<{ agentId: string; runId: string }> }) {
  const { agentId, runId } = await params;
  return <RunDetailPage agentId={agentId} runId={runId} />;
}
