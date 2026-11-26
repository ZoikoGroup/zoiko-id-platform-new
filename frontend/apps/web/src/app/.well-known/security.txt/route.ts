import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

/** Relays the cell's security.txt as it is (RFC 9116). When the cell serves none, neither do we: a missing or lapsed file is reported, not invented. */
export async function GET() {
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/.well-known/security.txt`, { cache: "no-store" });
    if (!res.ok) return new Response("Not found\n", { status: res.status === 503 ? 503 : 404, headers: { "content-type": "text/plain; charset=utf-8" } });
    return new Response(await res.text(), { status: 200, headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "public, max-age=300" } });
  } catch {
    return new Response("Unavailable\n", { status: 503, headers: { "content-type": "text/plain; charset=utf-8" } });
  }
}
