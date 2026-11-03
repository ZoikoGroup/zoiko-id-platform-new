import type { Metadata } from "next";

import { SignUp } from "@/features/sign-up/sign-up";

export const metadata: Metadata = { title: "Create your Zoiko iD" };

export default function SignUpPage() {
  return <SignUp />;
}
