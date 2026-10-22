import type { Metadata } from "next";

import { AccountsPage } from "@/features/admin/accounts/accounts-page";

export const metadata: Metadata = { title: "Sessions & recovery" };

export default function Page() {
  return <AccountsPage />;
}
