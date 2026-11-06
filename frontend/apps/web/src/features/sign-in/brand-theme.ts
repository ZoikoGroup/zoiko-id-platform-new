export type WorkspaceBrand = { accent: string; accent_hex: string; welcome_text: string; logo: string; support_url: string };
export const PLAIN_BRAND: WorkspaceBrand = { accent: "blue", accent_hex: "", welcome_text: "", logo: "", support_url: "" };

const HEX = /^#[0-9a-f]{6}$/i;
const IMAGE = /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/;

function shade(hex: string, by: number): string {
  const c = [1, 3, 5].map((i) => Math.round(Number.parseInt(hex.slice(i, i + 2), 16) * (1 - by)));
  return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

/** The brand colour variables for a page: the company's colour in place of the platform's, only when it is a plain #rrggbb. */
export function themeVars(hex: string): Record<string, string> {
  if (!HEX.test(hex)) return {};
  return { "--color-brand-500": hex, "--color-brand-600": hex, "--color-brand-700": shade(hex, 0.15), "--color-brand-900": shade(hex, 0.4) };
}

/** Only an inline PNG, JPEG or WebP is ever put in an image tag. */
export function safeLogo(v: string): string | null {
  return v.length <= 70_000 && IMAGE.test(v) ? v : null;
}

/** Reads the brand the cell sent, keeping only the fields of the right shape. */
export function parseBrand(raw: unknown): WorkspaceBrand {
  if (!raw || typeof raw !== "object") return PLAIN_BRAND;
  const r = raw as Record<string, unknown>;
  const s = (k: string) => (typeof r[k] === "string" ? (r[k] as string) : "");
  return { accent: s("accent") || "blue", accent_hex: s("accent_hex"), welcome_text: s("welcome_text").slice(0, 160), logo: s("logo"), support_url: s("support_url") };
}
