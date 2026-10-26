import type { Metadata } from "next";

import { ContactsPage } from "@/features/admin/contacts/contacts-page";

export const metadata: Metadata = { title: "Email contacts" };

export default function Page() {
  return <ContactsPage />;
}
