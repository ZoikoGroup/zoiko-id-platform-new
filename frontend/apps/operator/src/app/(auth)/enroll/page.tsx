import { redirect } from "next/navigation";

/** Operators sign in with an email and password only: there is no passkey enrollment on this console. */
export default function EnrollPage() {
  redirect("/sign-in");
}
