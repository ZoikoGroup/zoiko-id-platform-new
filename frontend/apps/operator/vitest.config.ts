import path from "node:path";

import { defineConfig } from "vitest/config";

// Next keeps JSX as written (tsconfig "jsx": "preserve"), so tests are told to compile it themselves. Component tests run in jsdom, like the web app's.
export default defineConfig({
  oxc: { jsx: { runtime: "automatic" } },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  // Same limit as the web app: form tests type character by character, which outlasts 5 s on a slow CI runner.
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"], testTimeout: 15_000 },
});
