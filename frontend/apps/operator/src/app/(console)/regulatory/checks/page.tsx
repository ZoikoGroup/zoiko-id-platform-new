import type { Metadata } from "next";

import { ChecksPage } from "@/features/regulatory/checks";

export const metadata: Metadata = { title: "Regulatory checks" };

export default function Page() {
  return <ChecksPage />;
}
