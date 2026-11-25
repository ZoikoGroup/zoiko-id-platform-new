import type { Metadata } from "next";

import { CommandCenter } from "@/features/command-center/command-center";
import { viewOf } from "@/features/command-center/logic";

export const metadata: Metadata = { title: "Command Center" };

export default async function Page({ params }: { params: Promise<{ view: string }> }) {
  return <CommandCenter view={viewOf((await params).view)} />;
}
