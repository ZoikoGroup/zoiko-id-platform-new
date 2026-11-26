import "server-only";

import { server } from "@/server/bff";

/** Read one public Trust Center feed from the cell. Only relays: a page can never say more than the register approved. A feed that cannot be read is null and the page says so, never an invented value. */
export async function trustFeed<T>(path: string): Promise<T | null> {
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}${path}`, { cache: "no-store", headers: { accept: "application/json" } });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}
