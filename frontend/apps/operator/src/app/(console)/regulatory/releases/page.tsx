import type { Metadata } from "next";

import { RetiredPage } from "@/features/regulatory/retired";

export const metadata: Metadata = { title: "Market releases" };

export default function Page() {
  return <RetiredPage title="Market releases" what="Per-country identity policies were released here." />;
}
