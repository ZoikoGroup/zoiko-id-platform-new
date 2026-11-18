import type { Metadata } from "next";

import { ChecksPage } from "@/features/admin/checks/checks-page";

export const metadata: Metadata = { title: "Verification checks" };

export default function Page() {
  return <ChecksPage />;
}
