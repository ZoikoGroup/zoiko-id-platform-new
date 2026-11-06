import { post } from "@/server/registration";

export const dynamic = "force-dynamic";

/** "I can't sign in": the cell emails the account's own address a way back, and answers every address the same way. */
export async function POST(req: Request) {
  return post(req, "/v1/registration/recovery", ["email"]);
}
