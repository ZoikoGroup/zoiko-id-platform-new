import type { Metadata } from "next";

import { PermissionsWorkbench } from "@/features/admin/agents/governance/permissions";

export const metadata: Metadata = { title: "Agent permissions" };

export default async function Page({ params }: { params: Promise<{ agentId: string }> }) {
  return <PermissionsWorkbench agentId={(await params).agentId} />;
}
