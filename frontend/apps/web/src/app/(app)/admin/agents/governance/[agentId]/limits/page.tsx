import type { Metadata } from "next";

import { LimitsEditor } from "@/features/admin/agents/governance/limits";

export const metadata: Metadata = { title: "Agent usage and spending" };

export default async function Page({ params }: { params: Promise<{ agentId: string }> }) {
  return <LimitsEditor agentId={(await params).agentId} />;
}
