// Shared ESLint flat config. Boundary rules from DEC-H1 / ZID-UX-001 §29, §36: the frontend never
// talks to databases or brokers, never persists secrets in web storage, and never renders raw HTML.
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export const boundaryRules = {
  "no-restricted-imports": [
    "error",
    {
      paths: [
        { name: "pg", message: "The frontend never touches a database (DEC-H1)." },
        { name: "kafkajs", message: "The frontend never talks to the event backbone (DEC-H1)." },
      ],
      patterns: [{ group: ["**/generated/*"], message: "Import the client from @zoiko/api-client." }],
    },
  ],
  "no-restricted-properties": [
    "error",
    { object: "window", property: "localStorage", message: "No tokens, secrets or evidence in web storage (§29)." },
    { object: "window", property: "sessionStorage", message: "No tokens, secrets or evidence in web storage (§29)." },
  ],
  "no-restricted-globals": [
    "error",
    { name: "localStorage", message: "No tokens, secrets or evidence in web storage (§29)." },
    { name: "sessionStorage", message: "No tokens, secrets or evidence in web storage (§29)." },
  ],
  "react/no-danger": "error",
};

export default tseslint.config(
  { ignores: ["**/.next/**", "**/dist/**", "**/generated/**", "**/next-env.d.ts"] },
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx,js,jsx,mjs}"],
    plugins: { react, "react-hooks": reactHooks },
    languageOptions: { parserOptions: { ecmaFeatures: { jsx: true } } },
    settings: { react: { version: "detect" } },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...boundaryRules,
    },
  },
);
