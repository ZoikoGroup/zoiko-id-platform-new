import type { Metadata } from "next";

import { Register } from "@/features/register/register";

export const metadata: Metadata = { title: "Register your company" };

export default function RegisterPage() {
  return <Register />;
}
