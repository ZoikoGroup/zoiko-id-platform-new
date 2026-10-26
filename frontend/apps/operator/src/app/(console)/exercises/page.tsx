import type { Metadata } from "next";

import { ExercisesPage } from "@/features/exercises";

export const metadata: Metadata = { title: "DR exercises" };

export default function Page() {
  return <ExercisesPage />;
}
