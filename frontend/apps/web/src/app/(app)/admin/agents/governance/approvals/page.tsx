import type { Metadata } from "next";

import { ApprovalCenter } from "@/features/admin/agents/governance/approvals";

export const metadata: Metadata = { title: "Agent change approvals" };

export default function Page() {
  return <ApprovalCenter />;
}
