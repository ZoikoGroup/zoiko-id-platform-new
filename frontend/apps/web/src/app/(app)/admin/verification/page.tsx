import type { Metadata } from "next";

import { VerificationAdminPage } from "@/features/admin/verification/page";

export const metadata: Metadata = { title: "Identity verification" };

export default function Page() {
  return <VerificationAdminPage />;
}
