import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

/** The OpenAPI description a partner downloads. It is the cell's own published document for the operations a partner's server calls; this only relays it, so the docs page and the API can never disagree. */
export async function GET() {
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/verify-api/openapi.json`, { cache: "no-store", headers: { accept: "application/json" } });
    if (!res.ok) return new Response("{}", { status: 502, headers: { "content-type": "application/json" } });
    return new Response(await res.text(), { status: 200, headers: { "content-type": "application/json", "cache-control": "public, max-age=300" } });
  } catch {
    return new Response("{}", { status: 503, headers: { "content-type": "application/json" } });
  }
}
