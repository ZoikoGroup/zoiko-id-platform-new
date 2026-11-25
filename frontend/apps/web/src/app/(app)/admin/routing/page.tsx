import type { Metadata } from "next";

import { RoutingPage } from "@/features/admin/routing/routing-page";

export const metadata: Metadata = { title: "Coverage & routing" };

export default function Page() {
  return <RoutingPage />;
}
