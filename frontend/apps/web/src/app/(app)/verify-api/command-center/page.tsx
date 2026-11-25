import type { Metadata } from "next";

import { CommandCenter } from "@/features/command-center/command-center";

export const metadata: Metadata = { title: "Command Center" };

export default function Page() {
  return <CommandCenter view="overview" />;
}
