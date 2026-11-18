import type { Metadata } from "next";

import { ScreeningSourcesPage } from "@/features/screening/sources";

export const metadata: Metadata = { title: "Sanctions sources" };

export default function Page() {
  return <ScreeningSourcesPage />;
}
