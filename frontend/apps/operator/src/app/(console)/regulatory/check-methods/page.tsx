import type { Metadata } from "next";

import { RetiredPage } from "@/features/regulatory/retired";

export const metadata: Metadata = { title: "Check methods" };

export default function Page() {
  return <RetiredPage title="Check methods" what="Per-country check methods were approved here." />;
}
