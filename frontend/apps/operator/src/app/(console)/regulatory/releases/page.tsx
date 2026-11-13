import type { Metadata } from "next";

import { ReleasesPage } from "@/features/regulatory/releases";

export const metadata: Metadata = { title: "Market releases" };

export default function Page() {
  return <ReleasesPage />;
}
