import { defineConfig } from "vitest/config";

// The package keeps JSX as written ("jsx": "react-jsx" for tsc); tests compile it themselves and run in jsdom.
export default defineConfig({
  oxc: { jsx: { runtime: "automatic" } },
  test: { environment: "jsdom", include: ["test/**/*.test.{ts,tsx}"], testTimeout: 15_000 },
});
