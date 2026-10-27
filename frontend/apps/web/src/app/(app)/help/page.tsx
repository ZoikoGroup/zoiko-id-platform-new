import type { Metadata } from "next";

import { HelpPage } from "@/features/help/help-page";

export const metadata: Metadata = { title: "Help & support" };

export default function Page() {
  return <HelpPage />;
}
