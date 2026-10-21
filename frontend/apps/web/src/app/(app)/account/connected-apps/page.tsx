import type { Metadata } from "next";

import { ConnectedAppsPage } from "@/features/account/connected-apps";

export const metadata: Metadata = { title: "Connected apps" };

export default function Page() {
  return <ConnectedAppsPage />;
}
