/** Server-side runtime settings for the web app. Read on the server only; fails fast when a
 * required value is missing rather than running half-configured. */

export type RuntimeEnv = {
  environment: "production" | "sandbox" | "test" | "development" | "local";
  /** Cell API base URL; reached only from the server, never from the browser. */
  cellApiUrl: string;
  /** Cell token issuer (ZID_TOKEN_ISSUER on the cell). */
  issuer: string;
  /** OAuth client registered for this app on the cell (first-party). */
  clientId: string;
  /** Public origin of this app; must be in the cell's ZID_WEBAUTHN_ORIGINS. */
  publicOrigin: string;
  /** OAuth redirect URI registered on the client; defaults to `${publicOrigin}/auth/callback`.
   * Local development registers http://127.0.0.1:3000/auth/callback (the cell accepts only https
   * or loopback-IP redirects) while the app runs on http://localhost:3000 (passkeys need a domain). */
  redirectUri?: string;
  /** Optional federation sign-in options: "connection_id=Label,connection_id=Label". */
  federationConnections: { id: string; label: string }[];
};

function required(env: Record<string, string | undefined>, key: string): string {
  const v = env[key];
  if (!v) {
    throw new Error(
      `${key} is required. For local development copy apps/web/.env.example to apps/web/.env.local and restart the dev server.`,
    );
  }
  return v.replace(/\/$/, "");
}

export function readRuntimeEnv(env: Record<string, string | undefined> = process.env): RuntimeEnv {
  const cellApiUrl = required(env, "ZID_CELL_API_URL");
  return {
    environment: (env.ZID_ENVIRONMENT ?? "development") as RuntimeEnv["environment"],
    cellApiUrl,
    issuer: (env.ZID_TOKEN_ISSUER ?? cellApiUrl).replace(/\/$/, ""),
    clientId: required(env, "ZID_OAUTH_CLIENT_ID"),
    publicOrigin: required(env, "ZID_PUBLIC_ORIGIN"),
    redirectUri: env.ZID_OAUTH_REDIRECT_URI || undefined,
    federationConnections: (env.ZID_FEDERATION_CONNECTIONS ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((pair) => {
        const [id, ...label] = pair.split("=");
        return { id: id!.trim(), label: label.join("=").trim() || "your organization" };
      }),
  };
}
