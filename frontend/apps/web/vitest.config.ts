import path from "node:path";

import { defineConfig } from "vitest/config";

// Next keeps JSX as written (tsconfig "jsx": "preserve"), so tests are told to compile it themselves.
export default defineConfig({
  oxc: { jsx: { runtime: "automatic" } },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  // 15 s a test: component tests that open dialogs take about 1 s alone but pass 5 s when a machine is busy (a developer's stack, a shared CI runner).
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"], testTimeout: 15_000 },
});
