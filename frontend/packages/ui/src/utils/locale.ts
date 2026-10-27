/** Which locale the console speaks (UX-31). It is decided once, on the server, from the browser's Accept-Language, and
 * handed to the page, so the server and the browser format a date or an amount the same way (no hydration mismatch). */

export const DEFAULT_LOCALE = "en-US";

/** The first language the browser prefers that this runtime can format, else the default. Never throws. */
export function resolveLocale(acceptLanguage: string | null | undefined, fallback: string = DEFAULT_LOCALE): string {
  if (!acceptLanguage) return fallback;
  const wanted = acceptLanguage
    .split(",")
    .map((part) => {
      const [tag = "", ...params] = part.trim().split(";");
      const q = params.map((p) => p.trim()).find((p) => p.startsWith("q="));
      return { tag: tag.trim(), q: q ? Number(q.slice(2)) : 1 };
    })
    .filter((x) => x.tag && x.tag !== "*" && Number.isFinite(x.q) && x.q > 0)
    .sort((a, b) => b.q - a.q);
  for (const { tag } of wanted) {
    try {
      const [ok] = Intl.NumberFormat.supportedLocalesOf(tag);
      if (ok) return ok;
    } catch {
      // a malformed tag is skipped, not fatal
    }
  }
  return fallback;
}
