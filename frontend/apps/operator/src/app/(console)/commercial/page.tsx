import type { Metadata } from "next";

import { CommercialPage } from "@/features/commercial/commercial-page";

export const metadata: Metadata = { title: "Commercial" };

export default function Page() {
  return <CommercialPage />;
}
