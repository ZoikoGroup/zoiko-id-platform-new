import type { Metadata } from "next";

import { RecoveryCodesPage } from "@/features/account/recovery-codes";

export const metadata: Metadata = { title: "Recovery codes" };

export default function Page() {
  return <RecoveryCodesPage />;
}
