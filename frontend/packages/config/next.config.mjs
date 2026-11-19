// Shared Next.js settings for every Zoiko iD app (DEC-H1, ZID-UX-001 §29).
// The nonce-based Content-Security-Policy is set per request in each app's `src/proxy.ts`;
// the static headers below never vary.

/** Headers that apply to every response, including static assets. */
export const securityHeaders = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()" },
];

/** The camera stays blocked everywhere (`camera=()` above). A page that photographs a document or a face names itself in `cameraPaths`, and only that page may ask for
 * the camera, and only for its own origin: never an embedded frame. The browser still asks the person, every time it is not already allowed. */
export const cameraHeader = { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()" };

/**
 * @param {{ extra?: import("next").NextConfig, cameraPaths?: string[] }} [opts]
 * @returns {import("next").NextConfig}
 */
export function zoikoNextConfig(opts = {}) {
  return {
    reactStrictMode: true,
    poweredByHeader: false,
    output: "standalone",
    transpilePackages: ["@zoiko/ui", "@zoiko/api-client", "@zoiko/auth-bff"],
    async headers() {
      // Next applies the rules in order and the last value of a header wins, so the camera rules come after the general one.
      return [
        { source: "/:path*", headers: securityHeaders },
        ...(opts.cameraPaths ?? []).map((source) => ({ source, headers: [cameraHeader] })),
      ];
    },
    ...opts.extra,
  };
}
