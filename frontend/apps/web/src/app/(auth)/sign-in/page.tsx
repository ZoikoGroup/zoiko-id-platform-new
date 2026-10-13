import type { Metadata } from "next";

import { SignIn } from "@/features/sign-in/sign-in";
import { server } from "@/server/bff";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignInPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const { env } = server();
  const sp = await searchParams;
  return <SignIn returnTo={sp.returnTo} error={sp.error} reason={sp.reason} connections={env.federationConnections} />;
}
