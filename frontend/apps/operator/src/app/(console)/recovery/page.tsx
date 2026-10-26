import type { Metadata } from "next";

import { RecoveryPage } from "@/features/recovery";

export const metadata: Metadata = { title: "Recovery operations" };

export default function Page() {
  return <RecoveryPage />;
}
