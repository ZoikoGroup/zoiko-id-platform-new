import type { Metadata } from "next";

import { SourceOfFundsCasePage } from "@/features/admin/source-of-funds/case-page";

export const metadata: Metadata = { title: "Source of funds case" };

export default async function Page({ params }: { params: Promise<{ caseId: string }> }) {
  return <SourceOfFundsCasePage caseId={(await params).caseId} />;
}
