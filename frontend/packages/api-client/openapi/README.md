# OpenAPI inputs

The contracts are not copied here: they live once, at the repository root in `contracts/openapi/`
(the backend's drift test keeps them equal to the code). `codegen.mjs` reads them and writes
`../src/generated/*.ts`. Run `npm run codegen` from `frontend/` after any contract change.
