// Shared ESLint flat config. Boundary rules from DEC-H1 / ZID-UX-001 §29, §36: the frontend never
// talks to databases or brokers, never persists secrets in web storage, and never renders raw HTML.
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

export default [{ rules: boundaryRules }];
