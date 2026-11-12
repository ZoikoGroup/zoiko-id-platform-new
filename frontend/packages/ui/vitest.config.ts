import { defineConfig } from "vitest/config";

// 15 s per test, as in the other packages that drive a user through a flow: the step-up draft test takes about 2.5 s alone and ran past the default
// 5 s when a busy machine ran every package's tests at once.
export default defineConfig({ test: { environment: "node", include: ["test/**/*.test.{ts,tsx}"], testTimeout: 15_000 } });
