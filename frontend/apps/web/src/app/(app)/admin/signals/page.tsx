import type { Metadata } from "next";

import { SignalsPage } from "@/features/admin/signals/signals-page";

export const metadata: Metadata = { title: "Security signals" };

export default function Page() {
  return <SignalsPage />;
}
