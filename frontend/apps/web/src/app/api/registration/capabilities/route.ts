import { get } from "@/server/registration";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  return get("/v1/registration/capabilities", { country: new URL(req.url).searchParams.get("country") });
}
