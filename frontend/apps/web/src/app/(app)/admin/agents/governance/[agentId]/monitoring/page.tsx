import type { Metadata } from "next";

import { MonitoringView } from "@/features/admin/agents/governance/monitoring";

export const metadata: Metadata = { title: "Agent monitoring" };

export default async function Page({ params }: { params: Promise<{ agentId: string }> }) {
  return <MonitoringView agentId={(await params).agentId} />;
}
