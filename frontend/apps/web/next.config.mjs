import { zoikoNextConfig } from "@zoiko/config/next";

// The assistant widget and the console guide ship as TypeScript source (a workspace package), so Next compiles it; this list replaces the shared default, so it repeats it.
export default zoikoNextConfig({
  // the only pages that photograph a document or a face
  cameraPaths: ["/account/identity-proofing", "/account/verification", "/verify/:ref*", "/sign-up/verify"],
  extra: { transpilePackages: ["@zoiko/ui", "@zoiko/api-client", "@zoiko/auth-bff", "@zoiko/assistant-widget", "@zoiko/console-guide"] },
});
