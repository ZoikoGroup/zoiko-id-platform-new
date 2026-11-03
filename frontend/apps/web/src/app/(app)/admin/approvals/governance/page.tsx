import type { Metadata } from "next";

import { GovernancePage } from "@/features/admin/approvals/approvals-pages";

export const metadata: Metadata = { title: "Governance and reviewers" };

export default function Page() {
  return <GovernancePage />;
}
