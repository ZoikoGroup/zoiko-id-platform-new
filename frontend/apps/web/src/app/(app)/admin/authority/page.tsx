import type { Metadata } from "next";

import { AuthorityPage } from "@/features/admin/authority/authority-page";

export const metadata: Metadata = { title: "Authority & approvals" };

export default function Page() {
  return <AuthorityPage />;
}
