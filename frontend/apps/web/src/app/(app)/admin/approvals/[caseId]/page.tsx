import type { Metadata } from "next";

import { ApprovalCasePage } from "@/features/admin/approvals/approvals-pages";

export const metadata: Metadata = { title: "Approval request" };

export default async function Page({ params }: { params: Promise<{ caseId: string }> }) {
  return <ApprovalCasePage caseId={(await params).caseId} />;
}
