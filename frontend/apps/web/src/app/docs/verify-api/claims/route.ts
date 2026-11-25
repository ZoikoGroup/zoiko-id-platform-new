import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

/** The public claims feed, relayed from the cell. Only relays: the Trust Center page and the API can never disagree about what may be said. */
export async function GET() {
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/verify-api/claims`, { cache: "no-store", headers: { accept: "application/json" } });
    if (!res.ok) return new Response("{}", { status: 502, headers: { "content-type": "application/json" } });
    return new Response(await res.text(), { status: 200, headers: { "content-type": "application/json", "cache-control": "public, max-age=300" } });
  } catch {
    return new Response("{}", { status: 503, headers: { "content-type": "application/json" } });
  }
}
