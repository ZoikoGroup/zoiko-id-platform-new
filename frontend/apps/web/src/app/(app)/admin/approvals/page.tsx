import type { Metadata } from "next";

import { ApprovalsInboxPage } from "@/features/admin/approvals/approvals-pages";

export const metadata: Metadata = { title: "Approvals" };

export default function Page() {
  return <ApprovalsInboxPage />;
}
